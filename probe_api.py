#!/usr/bin/env python3
"""
Quick API probe — tests every known Polymarket endpoint to find what's live.
Run this to see which endpoints work from your network.
"""

import requests
import json

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

def probe(name, url, params=None):
    try:
        resp = requests.get(url, params=params, timeout=10)
        status = resp.status_code
        if status == 200:
            data = resp.json()
            if isinstance(data, list):
                count = len(data)
            elif isinstance(data, dict):
                count = len(data.get("data", data.get("results", data)))
            else:
                count = "?"
            print(f"  ✓ {status}  {name:<45s}  records={count}")
            # Show first record keys
            sample = data[0] if isinstance(data, list) and data else data
            if isinstance(sample, dict):
                keys = list(sample.keys())[:10]
                print(f"         keys: {', '.join(keys)}")
            return data
        else:
            print(f"  ✗ {status}  {name}")
            return None
    except Exception as e:
        print(f"  ✗ ERR  {name:<45s}  {str(e)[:60]}")
        return None

print("=" * 65)
print("  POLYMARKET API PROBE")
print("=" * 65)

# ─── Gamma API ──────────────────────────────────────
print("\n── Gamma API ──────────────────────────────────")
probe("GET /markets", f"{GAMMA_API}/markets", {"limit": 2, "active": "true"})
probe("GET /events", f"{GAMMA_API}/events", {"limit": 2})

# ─── Data API — Leaderboard ─────────────────────────
print("\n── Data API — Leaderboard ─────────────────────")
for path in [
    "/leaderboard",
    "/v1/leaderboard",
    "/profit-leaderboard",
    "/rankings",
    "/leaderboard/ranking",
    "/leaderboard/profit",
    "/top-traders",
    "/v1/profit-leaderboard",
    "/v1/rankings",
]:
    probe(f"GET {path}", f"{DATA_API}{path}", {"limit": 3, "window": "all"})

# ─── Data API — Activity / Trades ───────────────────
print("\n── Data API — Activity / Trades ────────────────")
# We need a real address — try to get one from the leaderboard first
leaderboard_data = None
for path in ["/profit-leaderboard", "/leaderboard", "/v1/leaderboard", "/rankings"]:
    result = probe(f"GET {path} (for address)", f"{DATA_API}{path}", {"limit": 1, "window": "all"})
    if result:
        leaderboard_data = result
        break

test_address = None
if leaderboard_data:
    if isinstance(leaderboard_data, list) and leaderboard_data:
        entry = leaderboard_data[0]
    elif isinstance(leaderboard_data, dict):
        entries = leaderboard_data.get("data", leaderboard_data.get("results", []))
        entry = entries[0] if entries else {}
    else:
        entry = {}
    test_address = entry.get("proxyWallet", entry.get("address", entry.get("user", "")))
    print(f"\n  Using address from leaderboard: {test_address[:16]}…" if test_address else "")

if test_address:
    for path in ["/activity", "/trades", "/v1/activity", "/v1/trades"]:
        probe(f"GET {path}", f"{DATA_API}{path}", {"user": test_address, "limit": 3})
else:
    print("  ⚠ No test address available — skipping activity probes")

# ─── CLOB API ───────────────────────────────────────
print("\n── CLOB API ───────────────────────────────────")
probe("GET /markets", f"{CLOB_API}/markets", {"limit": 2})
probe("GET /sampling-markets", f"{CLOB_API}/sampling-markets")

print("\n" + "=" * 65)
print("  DONE — use the working endpoints above to update config.py")
print("=" * 65)
