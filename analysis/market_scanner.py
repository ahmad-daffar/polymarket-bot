"""
Market Scanner — Scan live markets for setups matching the extracted patterns.

A market is flagged when it matches multiple pattern criteria simultaneously:
  - Price in the winning entry range
  - Category matches a top-performing category
  - Adequate volume/liquidity
  - Resolution date within the typical winning timeframe
"""

import json
import pandas as pd
from datetime import datetime, timezone, timedelta

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from data.storage import Storage
from data.fetcher import fetch_active_markets


def scan_markets(storage: Storage, patterns: dict = None,
                 refresh: bool = True, verbose: bool = True) -> pd.DataFrame:
    """
    Scan active markets and score each one against the extracted patterns.
    Returns DataFrame of markets with match scores, sorted by score descending.
    """
    print(f"\n{'='*60}")
    print(f"  SCANNING LIVE MARKETS FOR PATTERN MATCHES")
    print(f"{'='*60}")

    # Refresh market data if requested
    if refresh:
        markets_df = fetch_active_markets()
        if not markets_df.empty:
            storage.save_markets(markets_df)
    else:
        markets_df = storage.get_active_markets()

    if markets_df.empty:
        print("  ✗ No active markets available.")
        return pd.DataFrame()

    # Load patterns if not provided
    if patterns is None:
        pattern_df = storage.get_patterns()
        if pattern_df.empty:
            print("  ✗ No patterns found. Run pattern extraction first.")
            return pd.DataFrame()
        patterns = {}
        for _, row in pattern_df.iterrows():
            patterns[row["pattern_type"]] = row["pattern_value"]

    # Extract pattern thresholds
    price_patterns = patterns.get("price_ranges", {})
    cat_patterns = patterns.get("categories", {})
    timing_patterns = patterns.get("timing", {})
    sizing_patterns = patterns.get("sizing", {})

    ideal_price_mean = price_patterns.get("mean", 0.40)
    ideal_price_std = price_patterns.get("std", 0.15)
    top_categories = set(list(cat_patterns.get("distribution", {}).keys())[:10])
    avg_hold_days = timing_patterns.get("avg_duration_days", 30)

    print(f"  Pattern criteria:")
    print(f"    Ideal entry price: {ideal_price_mean:.2f} ± {ideal_price_std:.2f}")
    print(f"    Top categories: {', '.join(list(top_categories)[:5])}")
    print(f"    Avg hold window: {avg_hold_days:.0f} days")
    print()

    scored_markets = []

    for _, market in markets_df.iterrows():
        scores = {}
        match_reasons = []

        # ─── 1. Price Match ─────────────────────────────────────────
        prices = market.get("outcome_prices", [])
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except:
                prices = []

        if prices:
            # Check if any outcome price is in the sweet spot
            best_price = None
            for p in prices:
                p = float(p)
                if config.MIN_AVG_ENTRY_PRICE <= p <= config.MAX_AVG_ENTRY_PRICE:
                    if best_price is None or abs(p - ideal_price_mean) < abs(best_price - ideal_price_mean):
                        best_price = p

            if best_price is not None:
                # Score based on distance from ideal
                distance = abs(best_price - ideal_price_mean)
                price_score = max(0, 1.0 - (distance / ideal_price_std))
                scores["price"] = price_score
                match_reasons.append(f"price={best_price:.2f}")
            else:
                scores["price"] = 0
        else:
            scores["price"] = 0

        # ─── 2. Category Match ──────────────────────────────────────
        category = market.get("category", "uncategorized")
        if category in top_categories:
            scores["category"] = 1.0
            match_reasons.append(f"cat={category}")
        else:
            scores["category"] = 0

        # ─── 3. Volume/Liquidity ────────────────────────────────────
        volume = float(market.get("volume", 0))
        liquidity = float(market.get("liquidity", 0))

        if volume > 10000 and liquidity > 1000:
            vol_score = min(1.0, volume / 100000)  # Normalize to 100k
            scores["volume"] = vol_score
            match_reasons.append(f"vol=${volume:,.0f}")
        elif volume > 1000:
            scores["volume"] = 0.3
        else:
            scores["volume"] = 0

        # ─── 4. Resolution Timing ───────────────────────────────────
        end_date = market.get("end_date")
        if pd.notna(end_date) and end_date:
            try:
                end_dt = pd.to_datetime(end_date, utc=True)
                days_until = (end_dt - datetime.now(timezone.utc)).days
                if 0 < days_until <= avg_hold_days * 2:
                    timing_score = 1.0 - abs(days_until - avg_hold_days) / (avg_hold_days * 2)
                    scores["timing"] = max(0, timing_score)
                    match_reasons.append(f"resolves={days_until}d")
                else:
                    scores["timing"] = 0
            except Exception:
                scores["timing"] = 0
        else:
            scores["timing"] = 0

        # ─── Composite Score ────────────────────────────────────────
        weights = {"price": 0.35, "category": 0.20, "volume": 0.20, "timing": 0.25}
        composite = sum(scores.get(k, 0) * w for k, w in weights.items())
        match_count = sum(1 for v in scores.values() if v > 0.3)

        scored_markets.append({
            "condition_id": market.get("condition_id", ""),
            "title": market.get("title", ""),
            "category": category,
            "volume": volume,
            "liquidity": liquidity,
            "outcome_prices": prices,
            "end_date": end_date,
            "match_score": round(composite, 4),
            "match_count": match_count,
            "price_score": round(scores.get("price", 0), 3),
            "category_score": round(scores.get("category", 0), 3),
            "volume_score": round(scores.get("volume", 0), 3),
            "timing_score": round(scores.get("timing", 0), 3),
            "match_reasons": match_reasons,
        })

    result_df = pd.DataFrame(scored_markets)
    result_df = result_df.sort_values("match_score", ascending=False).reset_index(drop=True)

    # Filter to meaningful matches
    good_matches = result_df[result_df["match_count"] >= config.ALERT_MIN_MATCH]

    if verbose:
        print(f"  Scanned {len(result_df)} markets:")
        print(f"    Strong matches (≥{config.ALERT_MIN_MATCH} criteria): {len(good_matches)}")
        print()

        if not good_matches.empty:
            print(f"  ── Top Matching Markets ({'─'*36})")
            for i, row in good_matches.head(15).iterrows():
                prices_str = ", ".join(f"{p:.2f}" for p in row["outcome_prices"][:2]) if row["outcome_prices"] else "N/A"
                reasons = " | ".join(row["match_reasons"])
                print(f"     [{row['match_score']:.3f}] {row['title'][:55]:<55s}")
                print(f"            prices=[{prices_str}] vol=${row['volume']:,.0f}  │  {reasons}")
            print()

    return result_df


def get_top_opportunities(storage: Storage, patterns: dict = None,
                          min_matches: int = None) -> pd.DataFrame:
    """
    Convenience function: scan and return only the best opportunities.
    """
    min_matches = min_matches or config.ALERT_MIN_MATCH
    all_markets = scan_markets(storage, patterns, verbose=False)
    if all_markets.empty:
        return pd.DataFrame()
    return all_markets[all_markets["match_count"] >= min_matches].head(20)
