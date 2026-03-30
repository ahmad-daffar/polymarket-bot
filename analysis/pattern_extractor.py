"""
Pattern Extractor — Analyze top wallets to find what winning traders have in common.

Extracts:
  1. Price range histograms (what entry prices they favor)
  2. Category specialization (which market types they focus on)
  3. Time-to-resolution on winning trades
  4. Position sizing patterns
  5. Win rate by category
"""

import numpy as np
import pandas as pd
from collections import Counter, defaultdict

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.storage import Storage


def extract_patterns(storage: Storage, top_n: int = 20, verbose: bool = True) -> dict:
    """
    Analyze the top N scored wallets and extract common trading patterns.
    Returns a dict of pattern summaries and saves them to storage.
    """
    print(f"\n{'='*60}")
    print(f"  EXTRACTING PATTERNS  (top {top_n} wallets)")
    print(f"{'='*60}")

    top_wallets = storage.get_top_wallets(limit=top_n)
    if top_wallets.empty:
        print("  ✗ No qualifying wallets found. Run scoring first.")
        return {}

    # Gather all trades from top wallets
    all_trades = []
    for _, w in top_wallets.iterrows():
        trades = storage.get_wallet_trades(w["address"])
        if not trades.empty:
            trades["wallet_score"] = w["score"]
            trades["wallet_wr"] = w["win_rate"]
            all_trades.append(trades)

    if not all_trades:
        print("  ✗ No trade data found for top wallets.")
        return {}

    trades_df = pd.concat(all_trades, ignore_index=True)
    trades_df["price"] = pd.to_numeric(trades_df["price"], errors="coerce")
    trades_df["size"] = pd.to_numeric(trades_df["size"], errors="coerce")
    trades_df["timestamp"] = pd.to_datetime(trades_df["timestamp"], utc=True, errors="coerce")

    print(f"  Analyzing {len(trades_df):,} trades from {len(top_wallets)} wallets\n")

    patterns = {}

    # ─── 1. Entry Price Distribution ────────────────────────────────
    patterns["price_ranges"] = _analyze_price_ranges(trades_df, verbose)

    # ─── 2. Category Specialization ─────────────────────────────────
    patterns["categories"] = _analyze_categories(trades_df, storage, verbose)

    # ─── 3. Time-to-Resolution on Winners ───────────────────────────
    patterns["timing"] = _analyze_timing(trades_df, verbose)

    # ─── 4. Position Sizing ─────────────────────────────────────────
    patterns["sizing"] = _analyze_sizing(trades_df, verbose)

    # ─── 5. Win Rate by Category ────────────────────────────────────
    patterns["category_win_rates"] = _analyze_category_win_rates(trades_df, storage, verbose)

    # ─── 6. Side Preference (BUY YES vs contrarian) ─────────────────
    patterns["side_preference"] = _analyze_side_preference(trades_df, verbose)

    # Save patterns to storage
    for ptype, pdata in patterns.items():
        storage.save_pattern(ptype, "summary", pdata, len(trades_df))

    print(f"\n  ✓ Extracted {len(patterns)} pattern categories")
    return patterns


def _analyze_price_ranges(df: pd.DataFrame, verbose: bool) -> dict:
    """Histogram of entry prices — what price ranges do winners favor?"""
    buy_prices = df[df["side"] == "BUY"]["price"].dropna()

    if buy_prices.empty:
        return {}

    bins = [0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.0]
    labels = ["0-10¢", "10-20¢", "20-30¢", "30-40¢", "40-50¢",
              "50-60¢", "60-70¢", "70-80¢", "80-90¢", "90-100¢"]

    hist = pd.cut(buy_prices, bins=bins, labels=labels, include_lowest=True)
    counts = hist.value_counts().sort_index()

    result = {
        "histogram": {k: int(v) for k, v in counts.items()},
        "mean": round(float(buy_prices.mean()), 4),
        "median": round(float(buy_prices.median()), 4),
        "std": round(float(buy_prices.std()), 4),
        "mode_bin": counts.idxmax() if not counts.empty else "N/A",
        "total_buys": len(buy_prices),
    }

    if verbose:
        print(f"  ── Entry Price Distribution ({'─'*35})")
        print(f"     Mean: {result['mean']:.2f}  |  Median: {result['median']:.2f}  |  Mode bin: {result['mode_bin']}")
        total = counts.sum()
        for label, count in counts.items():
            bar = "█" * int(count / max(total, 1) * 40)
            pct = count / total * 100 if total > 0 else 0
            print(f"     {label:>8s} │ {bar:<40s} {count:5d} ({pct:4.1f}%)")
        print()

    return result


def _analyze_categories(df: pd.DataFrame, storage: Storage, verbose: bool) -> dict:
    """Which market categories do top wallets specialize in?"""
    # Join with market data to get categories
    markets = storage.get_active_markets()
    if markets.empty:
        # Fall back to slug-based category extraction
        categories = df["slug"].apply(lambda s: s.split("-")[0] if isinstance(s, str) and s else "unknown")
    else:
        market_cats = markets.set_index("condition_id")["category"].to_dict()
        categories = df["condition_id"].map(market_cats).fillna("unknown")

    cat_counts = Counter(categories)
    total = sum(cat_counts.values())

    top_cats = cat_counts.most_common(15)
    result = {
        "distribution": {k: v for k, v in top_cats},
        "top_category": top_cats[0][0] if top_cats else "unknown",
        "concentration": top_cats[0][1] / total if top_cats and total > 0 else 0,
        "unique_categories": len(cat_counts),
    }

    if verbose:
        print(f"  ── Category Specialization ({'─'*33})")
        print(f"     Top: {result['top_category']}  |  {len(cat_counts)} unique categories")
        for cat, count in top_cats[:10]:
            bar = "█" * int(count / max(total, 1) * 40)
            pct = count / total * 100 if total > 0 else 0
            print(f"     {cat:>20s} │ {bar:<40s} {count:5d} ({pct:4.1f}%)")
        print()

    return result


def _analyze_timing(df: pd.DataFrame, verbose: bool) -> dict:
    """Analyze how long trades are held (time between first and last trade in a market)."""
    if "timestamp" not in df.columns:
        return {}

    # Group by wallet + market, find duration
    grouped = df.groupby(["wallet_address", "condition_id"]).agg(
        first_trade=("timestamp", "min"),
        last_trade=("timestamp", "max"),
        trade_count=("timestamp", "count"),
        avg_price=("price", "mean"),
    ).reset_index()

    grouped["duration_days"] = (
        pd.to_datetime(grouped["last_trade"]) - pd.to_datetime(grouped["first_trade"])
    ).dt.days

    result = {
        "avg_duration_days": round(float(grouped["duration_days"].mean()), 1),
        "median_duration_days": round(float(grouped["duration_days"].median()), 1),
        "avg_trades_per_market": round(float(grouped["trade_count"].mean()), 1),
        "pct_single_trade": round(float((grouped["trade_count"] == 1).mean()), 4),
        "positions_analyzed": len(grouped),
    }

    if verbose:
        print(f"  ── Timing Analysis ({'─'*41})")
        print(f"     Avg hold duration:    {result['avg_duration_days']:.1f} days")
        print(f"     Median hold duration: {result['median_duration_days']:.1f} days")
        print(f"     Avg trades/market:    {result['avg_trades_per_market']:.1f}")
        print(f"     Single-trade entries:  {result['pct_single_trade']:.1%}")
        print(f"     Positions analyzed:   {result['positions_analyzed']:,}")
        print()

    return result


def _analyze_sizing(df: pd.DataFrame, verbose: bool) -> dict:
    """Position sizing patterns — how much do winners bet?"""
    sizes = df["size"].dropna()
    sizes = sizes[sizes > 0]

    if sizes.empty:
        return {}

    percentiles = sizes.quantile([0.10, 0.25, 0.50, 0.75, 0.90]).to_dict()

    result = {
        "mean_size": round(float(sizes.mean()), 2),
        "median_size": round(float(sizes.median()), 2),
        "std_size": round(float(sizes.std()), 2),
        "p10": round(float(percentiles.get(0.10, 0)), 2),
        "p25": round(float(percentiles.get(0.25, 0)), 2),
        "p50": round(float(percentiles.get(0.50, 0)), 2),
        "p75": round(float(percentiles.get(0.75, 0)), 2),
        "p90": round(float(percentiles.get(0.90, 0)), 2),
    }

    if verbose:
        print(f"  ── Position Sizing ({'─'*41})")
        print(f"     Mean:   ${result['mean_size']:>10,.2f}")
        print(f"     Median: ${result['median_size']:>10,.2f}")
        print(f"     P10:    ${result['p10']:>10,.2f}  |  P90: ${result['p90']:>10,.2f}")
        print(f"     P25:    ${result['p25']:>10,.2f}  |  P75: ${result['p75']:>10,.2f}")
        print()

    return result


def _analyze_category_win_rates(df: pd.DataFrame, storage: Storage, verbose: bool) -> dict:
    """Win rate broken down by market category."""
    markets = storage.get_active_markets()

    if not markets.empty:
        market_cats = markets.set_index("condition_id")["category"].to_dict()
        df = df.copy()
        df["category"] = df["condition_id"].map(market_cats).fillna("unknown")
    else:
        df = df.copy()
        df["category"] = df["slug"].apply(lambda s: s.split("-")[0] if isinstance(s, str) and s else "unknown")

    # Estimate wins: price < 0.5 for BUY is "value territory"
    df["is_value_buy"] = (df["side"] == "BUY") & (df["price"] < 0.50)

    cat_stats = df.groupby("category").agg(
        total_trades=("price", "count"),
        avg_price=("price", "mean"),
        value_buys=("is_value_buy", "sum"),
    ).reset_index()

    cat_stats["value_buy_rate"] = cat_stats["value_buys"] / cat_stats["total_trades"]
    cat_stats = cat_stats.sort_values("total_trades", ascending=False)

    result = {
        "by_category": cat_stats.head(15).to_dict(orient="records"),
        "best_value_category": cat_stats.sort_values("value_buy_rate", ascending=False).iloc[0]["category"]
        if not cat_stats.empty else "unknown",
    }

    if verbose:
        print(f"  ── Win Rate by Category ({'─'*36})")
        for _, row in cat_stats.head(10).iterrows():
            print(f"     {row['category']:>20s} │ {row['total_trades']:5d} trades │ "
                  f"avg_price={row['avg_price']:.2f} │ value_buy_rate={row['value_buy_rate']:.1%}")
        print()

    return result


def _analyze_side_preference(df: pd.DataFrame, verbose: bool) -> dict:
    """Do winners prefer buying YES or selling?"""
    side_counts = df["side"].value_counts().to_dict()
    total = sum(side_counts.values())

    result = {
        "counts": side_counts,
        "buy_pct": side_counts.get("BUY", 0) / total if total > 0 else 0,
        "sell_pct": side_counts.get("SELL", 0) / total if total > 0 else 0,
    }

    if verbose:
        print(f"  ── Side Preference ({'─'*41})")
        for side, count in side_counts.items():
            pct = count / total * 100 if total > 0 else 0
            print(f"     {side:>6s}: {count:6d} ({pct:.1f}%)")
        print()

    return result


def get_pattern_summary(patterns: dict) -> str:
    """Generate a human-readable pattern summary."""
    lines = []
    lines.append("PATTERN SUMMARY — What Winning Wallets Have in Common")
    lines.append("=" * 55)

    pr = patterns.get("price_ranges", {})
    if pr:
        lines.append(f"\n  Entry Prices:")
        lines.append(f"    Most common range: {pr.get('mode_bin', 'N/A')}")
        lines.append(f"    Average entry: {pr.get('mean', 0):.2f}  |  Median: {pr.get('median', 0):.2f}")

    cat = patterns.get("categories", {})
    if cat:
        lines.append(f"\n  Market Focus:")
        lines.append(f"    Top category: {cat.get('top_category', 'N/A')} "
                      f"({cat.get('concentration', 0):.1%} of trades)")
        lines.append(f"    Unique categories traded: {cat.get('unique_categories', 0)}")

    timing = patterns.get("timing", {})
    if timing:
        lines.append(f"\n  Timing:")
        lines.append(f"    Avg hold: {timing.get('avg_duration_days', 0):.1f} days")
        lines.append(f"    Single-entry rate: {timing.get('pct_single_trade', 0):.1%}")

    sizing = patterns.get("sizing", {})
    if sizing:
        lines.append(f"\n  Position Sizing:")
        lines.append(f"    Median bet: ${sizing.get('median_size', 0):,.2f}")
        lines.append(f"    P25-P75 range: ${sizing.get('p25', 0):,.2f} – ${sizing.get('p75', 0):,.2f}")

    side = patterns.get("side_preference", {})
    if side:
        lines.append(f"\n  Side Preference:")
        lines.append(f"    Buy YES: {side.get('buy_pct', 0):.1%}  |  Sell: {side.get('sell_pct', 0):.1%}")

    return "\n".join(lines)
