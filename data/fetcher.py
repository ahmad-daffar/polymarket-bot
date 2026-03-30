"""
Data Fetcher — Pull data from Polymarket public APIs.

Endpoints used (all public, no auth required):
  Gamma API  : https://gamma-api.polymarket.com
  Data API   : https://data-api.polymarket.com
  CLOB API   : https://clob.polymarket.com
"""

import time
import json
import requests
import pandas as pd
from datetime import datetime, timezone

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config


# ─── Helpers ────────────────────────────────────────────────────────────────

def _get(url: str, params: dict = None, retries: int = 3, delay: float = 1.0):
    """GET with retries and rate-limit backoff."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = delay * (2 ** attempt)
                print(f"  ⏳ Rate-limited, waiting {wait:.0f}s …")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            if attempt == retries - 1:
                print(f"  ✗ Request failed after {retries} attempts: {e}")
                return None
            time.sleep(delay)
    return None


# ─── Leaderboard ────────────────────────────────────────────────────────────

def fetch_leaderboard(window: str = None, limit: int = None) -> pd.DataFrame:
    """
    Fetch top traders from the Polymarket profit leaderboard.
    Returns DataFrame with columns: rank, address, username, pnl, volume.
    """
    window = window or config.LEADERBOARD_WINDOW
    limit = limit or config.LEADERBOARD_LIMIT

    print(f"\n{'='*60}")
    print(f"  FETCHING LEADERBOARD  (window={window}, limit={limit})")
    print(f"{'='*60}")

    # Confirmed working endpoint first (from probe_api.py results)
    endpoints = [
        (f"{config.DATA_API}/v1/leaderboard", {"window": window, "limit": limit, "offset": 0}),
        (f"{config.DATA_API}/profit-leaderboard", {"window": window, "limit": limit, "offset": 0}),
        (f"{config.DATA_API}/rankings", {"window": window, "limit": limit, "offset": 0, "orderBy": "pnl"}),
        (f"{config.DATA_API}/leaderboard/ranking", {"window": window, "limit": limit, "offset": 0}),
        (f"{config.DATA_API}/leaderboard", {"window": window, "limit": limit, "offset": 0}),
    ]

    data = None
    for url, params in endpoints:
        print(f"  Trying {url.split('.com')[1]} …", end=" ")
        result = _get(url, params, retries=1, delay=0.5)
        if result is not None:
            data = result
            print("✓")
            break
        else:
            print("✗")

    if not data:
        print("  ✗ All leaderboard endpoints failed.")
        return pd.DataFrame()

    # The response may be a list directly or nested under a key
    records = data if isinstance(data, list) else data.get("results", data.get("data", []))

    rows = []
    for i, entry in enumerate(records):
        rows.append({
            "rank": entry.get("rank", i + 1),
            "address": entry.get("proxyWallet", entry.get("address", "")),
            "username": entry.get("userName", entry.get("username", entry.get("pseudonym", "anon"))),
            "pnl": float(entry.get("pnl", entry.get("profit", 0))),
            "volume": float(entry.get("vol", entry.get("volume", 0))),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        print("  ✗ Leaderboard returned no data.")
        return df

    df = df.sort_values("pnl", ascending=False).reset_index(drop=True)
    print(f"  ✓ Fetched {len(df)} wallets from leaderboard")
    print(f"    Top PnL: ${df['pnl'].iloc[0]:,.2f}  |  Bottom PnL: ${df['pnl'].iloc[-1]:,.2f}")
    return df


# ─── Wallet Activity / Trade History ────────────────────────────────────────

def fetch_wallet_activity(address: str, limit: int = None) -> pd.DataFrame:
    """
    Fetch full trade history for a single wallet.
    Returns DataFrame with: timestamp, market, side, price, size, outcome, type.
    """
    limit = limit or config.ACTIVITY_FETCH_LIMIT

    # Confirmed working endpoints first (from probe_api.py results)
    activity_endpoints = [
        f"{config.DATA_API}/activity",
        f"{config.DATA_API}/trades",
        f"{config.DATA_API}/v1/activity",
        f"{config.DATA_API}/v1/trades",
    ]

    working_url = None
    for test_url in activity_endpoints:
        test_data = _get(test_url, {"user": address, "limit": 1}, retries=1, delay=0.3)
        if test_data is not None:
            working_url = test_url
            break

    if working_url is None:
        return pd.DataFrame()

    all_records = []
    offset = 0
    MAX_OFFSET = 3000  # API returns 400 past ~3500, stop safely before that

    while offset <= MAX_OFFSET:
        params = {
            "user": address,
            "limit": limit,
            "offset": offset,
            "type": "TRADE",
        }
        data = _get(working_url, params, retries=1)
        if not data:
            break

        records = data if isinstance(data, list) else data.get("results", data.get("data", []))
        if not records:
            break

        all_records.extend(records)
        if len(records) < limit:
            break
        offset += limit
        time.sleep(0.3)  # Be polite

    if not all_records:
        return pd.DataFrame()

    rows = []
    for rec in all_records:
        ts_raw = rec.get("timestamp", rec.get("createdAt", ""))
        try:
            if isinstance(ts_raw, (int, float)):
                ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
            else:
                ts = pd.to_datetime(ts_raw, utc=True)
        except Exception:
            ts = pd.NaT

        rows.append({
            "timestamp": ts,
            "condition_id": rec.get("conditionId", rec.get("condition_id", "")),
            "market_title": rec.get("title", rec.get("market", "unknown")),
            "side": rec.get("side", "").upper(),
            "price": float(rec.get("price", 0)),
            "size": float(rec.get("usdcSize", rec.get("size", 0))),
            "outcome": rec.get("outcome", ""),
            "outcome_index": rec.get("outcomeIndex", None),
            "slug": rec.get("slug", rec.get("eventSlug", "")),
            "tx_hash": rec.get("transactionHash", ""),
        })

    df = pd.DataFrame(rows)
    if not df.empty and "timestamp" in df.columns:
        df = df.sort_values("timestamp").reset_index(drop=True)

    return df


def fetch_wallet_positions(address: str) -> pd.DataFrame:
    """Fetch current open positions for a wallet."""
    url = f"{config.DATA_API}/positions"
    params = {"user": address, "limit": 500, "sizeThreshold": 0.01}
    data = _get(url, params)

    if not data:
        return pd.DataFrame()

    records = data if isinstance(data, list) else data.get("results", data.get("data", []))
    if not records:
        return pd.DataFrame()

    rows = []
    for rec in records:
        rows.append({
            "condition_id": rec.get("conditionId", rec.get("condition_id", "")),
            "market_title": rec.get("title", rec.get("market", "")),
            "size": float(rec.get("size", 0)),
            "avg_price": float(rec.get("avgPrice", rec.get("price", 0))),
            "current_value": float(rec.get("currentValue", rec.get("value", 0))),
            "outcome": rec.get("outcome", ""),
            "outcome_index": rec.get("outcomeIndex", None),
            "redeemable": rec.get("redeemable", False),
        })

    return pd.DataFrame(rows)


# ─── Active Markets ─────────────────────────────────────────────────────────

def fetch_active_markets(limit: int = None) -> pd.DataFrame:
    """
    Fetch currently active markets from Gamma API.
    Returns DataFrame with: condition_id, title, slug, volume, liquidity,
    outcome_prices, end_date, category, active.
    """
    limit = limit or config.MARKET_FETCH_LIMIT

    print(f"\n{'='*60}")
    print(f"  FETCHING ACTIVE MARKETS")
    print(f"{'='*60}")

    url = f"{config.GAMMA_API}/markets"
    all_records = []
    offset = 0
    max_pages = 10  # Safety cap

    for page in range(max_pages):
        params = {
            "limit": limit,
            "offset": offset,
            "active": "true",
            "closed": "false",
        }
        data = _get(url, params)
        if not data:
            break

        records = data if isinstance(data, list) else data.get("results", data.get("data", []))
        if not records:
            break

        all_records.extend(records)
        print(f"    page {page+1}: got {len(records)} markets (total: {len(all_records)})")

        if len(records) < limit:
            break
        offset += limit
        time.sleep(0.3)

    if not all_records:
        print("  ✗ No active markets found.")
        return pd.DataFrame()

    rows = []
    for m in all_records:
        # Parse outcome prices (stored as JSON string like '["0.65","0.35"]')
        prices_raw = m.get("outcomePrices", "[]")
        try:
            if isinstance(prices_raw, str):
                prices = json.loads(prices_raw)
            else:
                prices = prices_raw
            prices = [float(p) for p in prices]
        except Exception:
            prices = []

        # Parse clob token IDs
        token_ids_raw = m.get("clobTokenIds", "[]")
        try:
            if isinstance(token_ids_raw, str):
                token_ids = json.loads(token_ids_raw)
            else:
                token_ids = token_ids_raw
        except Exception:
            token_ids = []

        # Tags / category
        tags = m.get("tags", [])
        if isinstance(tags, list) and tags:
            if isinstance(tags[0], dict):
                category = tags[0].get("label", tags[0].get("slug", "uncategorized"))
            else:
                category = str(tags[0])
        else:
            category = "uncategorized"

        end_date_raw = m.get("endDate", m.get("end_date", ""))
        try:
            end_date = pd.to_datetime(end_date_raw, utc=True) if end_date_raw else pd.NaT
        except Exception:
            end_date = pd.NaT

        rows.append({
            "condition_id": m.get("conditionId", m.get("condition_id", "")),
            "title": m.get("question", m.get("title", "")),
            "slug": m.get("slug", ""),
            "volume": float(m.get("volume", m.get("volumeNum", 0)) or 0),
            "liquidity": float(m.get("liquidity", m.get("liquidityNum", 0)) or 0),
            "outcome_prices": prices,
            "clob_token_ids": token_ids,
            "end_date": end_date,
            "category": category,
            "active": m.get("active", True),
            "outcomes": m.get("outcomes", ""),
        })

    df = pd.DataFrame(rows)
    print(f"  ✓ Fetched {len(df)} active markets")
    if not df.empty:
        print(f"    Total volume: ${df['volume'].sum():,.0f}")
    return df


# ─── CLOB Price Data ────────────────────────────────────────────────────────

def fetch_market_price(token_id: str) -> dict:
    """Fetch current best bid/ask/mid for a CLOB token."""
    url = f"{config.CLOB_API}/price"
    params = {"token_id": token_id}
    data = _get(url, params)
    if data:
        return {
            "bid": float(data.get("bid", 0)),
            "ask": float(data.get("ask", 0)),
            "mid": float(data.get("mid", (float(data.get("bid", 0)) + float(data.get("ask", 0))) / 2)),
        }
    return {"bid": 0, "ask": 0, "mid": 0}


def fetch_price_history(token_id: str, interval: str = "1d", fidelity: int = 60) -> pd.DataFrame:
    """Fetch price history for a token from CLOB."""
    url = f"{config.CLOB_API}/prices-history"
    params = {"market": token_id, "interval": interval, "fidelity": fidelity}
    data = _get(url, params)

    if not data or not isinstance(data, dict):
        return pd.DataFrame()

    history = data.get("history", [])
    if not history:
        return pd.DataFrame()

    rows = []
    for point in history:
        rows.append({
            "timestamp": pd.to_datetime(point.get("t", 0), unit="s", utc=True),
            "price": float(point.get("p", 0)),
        })

    return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)


# ─── Quick Test ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing Polymarket Data Fetcher …\n")

    lb = fetch_leaderboard(limit=10)
    if not lb.empty:
        print(f"\n  Sample leaderboard data:\n{lb.head().to_string(index=False)}\n")

        # Test wallet activity for the top wallet
        top_addr = lb.iloc[0]["address"]
        print(f"\n  Fetching activity for top wallet: {top_addr[:10]}…")
        activity = fetch_wallet_activity(top_addr)
        if not activity.empty:
            print(f"  ✓ Got {len(activity)} trades")
            print(f"    Date range: {activity['timestamp'].min()} → {activity['timestamp'].max()}")
            print(f"    Avg entry price: {activity['price'].mean():.3f}")
        else:
            print("  ✗ No activity data returned")

    markets = fetch_active_markets(limit=20)
    if not markets.empty:
        print(f"\n  Sample markets:\n{markets[['title','volume','category']].head(5).to_string(index=False)}")

    print("\n✓ Fetcher test complete.")
