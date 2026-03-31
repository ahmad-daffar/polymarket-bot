#!/usr/bin/env python3
"""
Polymarket Research & Simulation System
========================================

Entry point. Runs the full pipeline:
  1. Fetch top wallets from leaderboard
  2. Score each wallet (S(w) = α·PnL + β·Consistency − γ·MaxDrawdown)
  3. Extract patterns from top-scoring wallets
  4. Scan live markets for pattern matches
  5. (Optional) Run paper-trading simulation

Usage:
  python main.py                  # Full research pipeline
  python main.py --simulate       # Research + paper trading simulation
  python main.py --live           # Placeholder for Phase 2
  python main.py --quick          # Quick mode: fewer wallets, faster
  python main.py --demo           # Demo mode with synthetic data (no API needed)
  python main.py --demo --simulate  # Demo with paper trading simulation
"""

import argparse
import random
import sys
import os
import time
import pandas as pd
from datetime import datetime, timezone

# Ensure imports work from project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from data.fetcher import fetch_leaderboard, fetch_active_markets, fetch_wallet_activity
from data.storage import Storage
from data.wallet_scorer import score_wallets
from data.demo_data import generate_leaderboard, generate_wallet_trades, generate_active_markets
from analysis.pattern_extractor import extract_patterns, get_pattern_summary
from analysis.market_scanner import scan_markets
from simulation.paper_trader import PaperTrader
from simulation.performance import PerformanceTracker
from alerts.notifier import Notifier
from dashboard import generate_dashboard


def print_banner():
    print("""
╔══════════════════════════════════════════════════════════════╗
║           POLYMARKET RESEARCH & SIMULATION SYSTEM           ║
║                                                              ║
║  Wallet Scoring · Pattern Extraction · Market Scanning       ║
║  Paper Trading · Performance Analytics                       ║
╚══════════════════════════════════════════════════════════════╝
    """)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"  Started at: {now}")
    print(f"  Config: bankroll=${config.STARTING_BANKROLL:,.0f} | "
          f"Kelly={config.KELLY_FRACTION} | "
          f"Scoring: α={config.ALPHA} β={config.BETA} γ={config.GAMMA}")


def print_db_stats(storage: Storage):
    stats = storage.stats()
    print(f"\n  Database: {stats['wallets']} wallets ({stats['wallets_passing']} passing) | "
          f"{stats['trades']:,} trades | {stats['markets']} markets | "
          f"{stats['simulated_trades']} sim trades | {stats['patterns']} patterns")


def run_research(storage: Storage, notifier: Notifier, quick: bool = False):
    """Run the full research pipeline: fetch → score → extract → scan."""

    # ─── Step 1: Fetch Leaderboard ──────────────────────────────────
    limit = 25 if quick else config.LEADERBOARD_LIMIT
    leaderboard = fetch_leaderboard(limit=limit)

    if leaderboard.empty:
        print("\n  ✗ Could not fetch leaderboard. Check network / API status.")
        print("    Continuing with cached data if available …")
        cached_wallets = storage.get_all_wallets()
        if cached_wallets.empty:
            print("    No cached data either. Exiting.")
            return None, None, None
        leaderboard = cached_wallets

    # ─── Step 2: Score Wallets ──────────────────────────────────────
    max_score = 10 if quick else min(len(leaderboard), config.LEADERBOARD_LIMIT)
    scored = score_wallets(leaderboard, storage, max_wallets=max_score)

    # Show top 20
    passing = scored[scored["passes_filters"] == True].head(config.TOP_WALLETS_TO_ANALYZE)
    if not passing.empty:
        print(f"\n{'='*60}")
        print(f"  TOP {len(passing)} SCORED WALLETS")
        print(f"{'='*60}")
        display_cols = ["username", "score", "pnl", "total_trades", "win_rate",
                        "avg_entry_price", "max_drawdown", "consistency"]
        available_cols = [c for c in display_cols if c in passing.columns]
        print(passing[available_cols].to_string(index=False))
    else:
        print("\n  ⚠ No wallets passed all filters.")
        print("    This can happen if the API returns limited trade history.")
        print("    Relaxing filters or using cached data …")

    # ─── Step 3: Extract Patterns ───────────────────────────────────
    patterns = extract_patterns(storage, top_n=config.TOP_WALLETS_TO_ANALYZE)

    if patterns:
        print(f"\n{get_pattern_summary(patterns)}")

    # ─── Step 4: Scan Live Markets ──────────────────────────────────
    matched_markets = scan_markets(storage, patterns)

    # Trigger alerts for strong matches
    if not matched_markets.empty:
        strong = matched_markets[matched_markets["match_count"] >= config.ALERT_MIN_MATCH]
        for _, mkt in strong.head(10).iterrows():
            notifier.alert_market_match(
                market=mkt.to_dict(),
                score=mkt["match_score"],
                reasons=mkt["match_reasons"],
            )

    return scored, patterns, matched_markets


def run_forward_simulation(storage: Storage, notifier: Notifier, patterns: dict = None):
    """
    Forward-only paper trading cycle. Each 2-hour call:
      1. Resolves any open positions that settled (price ≥ 0.98 or ≤ 0.02).
      2. For each top wallet, fetches ONLY trades newer than last_trade_ts.
      3. Calls run_forward_cycle — no historical backtest, ever.
    """
    print(f"\n{'='*60}")
    print(f"  FORWARD PAPER TRADING CYCLE")
    print(f"{'='*60}")

    trader = PaperTrader(storage)
    perf   = PerformanceTracker(storage)

    # Record forward start date on the very first run
    if not storage.get_state("forward_start_date"):
        now_iso = datetime.now(timezone.utc).isoformat()
        storage.set_state("forward_start_date", now_iso)
        print(f"  🏁 First run! Forward simulation started at {now_iso[:16]} UTC")

    print(f"  Capital: ${trader.bankroll:,.2f}  |  Open positions: {len(trader.positions)}")
    print(f"  Kelly fraction: {config.KELLY_FRACTION}  |  "
          f"Max position: {config.MAX_POSITION_PCT:.0%}  |  "
          f"Warm-up trades needed: {config.MIN_FORWARD_TRADES}")
    print()

    # ─── Step 1: Resolve settled positions ─────────────────────────────────
    print(f"  Checking open positions for resolution …")
    resolved = trader.check_and_resolve_open_positions(verbose=True)
    if not resolved:
        print(f"    (no positions resolved this cycle)")

    # ─── Step 2: Mirror new wallet trades ──────────────────────────────────
    top_wallets = storage.get_top_wallets(limit=config.TOP_WALLETS_TO_ANALYZE)
    if top_wallets.empty:
        print("  ✗ No qualifying wallets to mirror. Run research pipeline first.")
        return

    now_ts = int(time.time())

    total_executed = 0
    for _, wallet in top_wallets.iterrows():
        addr     = wallet["address"]
        username = wallet.get("username", "anon")
        score    = wallet.get("score", 0)

        last_ts = storage.get_wallet_last_ts(addr)
        if last_ts > 0:
            # Normal case: only look at trades since last time we checked this wallet
            since_ts = last_ts
        else:
            # First time seeing this wallet: look back 24h to catch recent activity
            since_ts = now_ts - 86400

        since_str = datetime.fromtimestamp(since_ts, tz=timezone.utc).strftime("%H:%M UTC")
        print(f"\n  ── {username} (score={score:.3f}) ── since {since_str}")

        raw_trades = fetch_wallet_activity(addr)
        if raw_trades.empty:
            print(f"     No activity returned from API.")
            continue

        # Filter to only trades newer than since_ts
        if "timestamp" in raw_trades.columns:
            raw_trades["_ts_unix"] = (
                pd.to_datetime(raw_trades["timestamp"], utc=True, errors="coerce")
                .astype("int64") // 1_000_000_000
            )
            new_df = raw_trades[raw_trades["_ts_unix"] > since_ts]
            new_trades = new_df.to_dict("records")
        else:
            new_trades = []

        print(f"     {len(new_trades)} new trade(s) in window")

        if new_trades:
            executed = trader.run_forward_cycle(addr, new_trades, patterns, verbose=True)
            total_executed += len(executed)

            for trade in executed:
                notifier.alert_sim_trade(trade, trader.bankroll)

            # Advance the wallet's last-seen timestamp
            newest_ts = max(t.get("_ts_unix", since_ts) for t in new_trades)
            storage.update_wallet_state(addr, last_trade_ts=int(newest_ts))

        # Drawdown warning
        portfolio = trader.get_portfolio_summary()
        if portfolio["peak_bankroll"] > 0:
            dd = 1 - (portfolio["bankroll"] / portfolio["peak_bankroll"])
            if dd > 0.15:
                notifier.alert_performance_milestone("drawdown", dd, 0.15)

    # ─── Performance Report ─────────────────────────────────────────────────
    print(f"\n  Trades executed this cycle: {total_executed}")
    metrics = perf.compute_metrics(verbose=True)
    return metrics


def run_demo_simulation(storage: Storage, notifier: Notifier, patterns: dict = None):
    """Demo mode: feed all cached synthetic trades through the forward cycle (no API)."""
    print(f"\n{'='*60}")
    print(f"  DEMO PAPER TRADING SIMULATION")
    print(f"{'='*60}")
    print(f"  Bankroll: ${config.STARTING_BANKROLL:,.2f}  |  "
          f"Kelly: {config.KELLY_FRACTION}  |  Max pos: {config.MAX_POSITION_PCT:.0%}")
    print()

    trader = PaperTrader(storage)
    perf   = PerformanceTracker(storage)

    top_wallets = storage.get_top_wallets(limit=config.TOP_WALLETS_TO_ANALYZE)
    if top_wallets.empty:
        print("  ✗ No qualifying wallets to simulate from.")
        return

    print(f"  Simulating {len(top_wallets)} wallets with synthetic trades …\n")

    total_executed = 0
    for _, wallet in top_wallets.iterrows():
        addr     = wallet["address"]
        username = wallet.get("username", "anon")
        score    = wallet.get("score", 0)

        print(f"  ── {username} (score={score:.3f}) ──")
        trades = storage.get_wallet_trades(addr)
        if trades.empty:
            print(f"     No cached trades. Skipping.")
            continue

        new_trades = trades.to_dict("records")
        executed   = trader.run_forward_cycle(addr, new_trades, patterns, verbose=True)
        total_executed += len(executed)

        for trade in executed:
            notifier.alert_sim_trade(trade, trader.bankroll)

        portfolio = trader.get_portfolio_summary()
        if portfolio["peak_bankroll"] > 0:
            dd = 1 - (portfolio["bankroll"] / portfolio["peak_bankroll"])
            if dd > 0.15:
                notifier.alert_performance_milestone("drawdown", dd, 0.15)

    print(f"\n  Total simulated trades: {total_executed}")
    metrics = perf.compute_metrics(verbose=True)
    return metrics


def run_demo(storage: Storage, notifier: Notifier, simulate: bool = False):
    """Run the full pipeline with synthetic demo data (no API calls)."""
    print(f"\n  🧪 DEMO MODE — Using synthetic data (no API calls)")
    print(f"     To use real Polymarket data, run without --demo\n")

    # ─── Step 1: Generate leaderboard ───────────────────────────────
    print(f"{'='*60}")
    print(f"  GENERATING DEMO LEADERBOARD")
    print(f"{'='*60}")
    leaderboard = generate_leaderboard(n=50)
    print(f"  ✓ Generated {len(leaderboard)} wallets")
    print(f"    Top PnL: ${leaderboard['pnl'].iloc[0]:,.2f}  |  Median: ${leaderboard['pnl'].median():,.2f}")

    # ─── Step 2: Score wallets ──────────────────────────────────────
    # Instead of fetching from API, generate trade histories
    print(f"\n{'='*60}")
    print(f"  SCORING WALLETS  ({min(30, len(leaderboard))} wallets)")
    print(f"{'='*60}")
    print(f"  Formula: S(w) = {config.ALPHA}·PnL + {config.BETA}·Consistency − {config.GAMMA}·MaxDrawdown")
    print()

    from data.wallet_scorer import analyze_wallet

    results = []
    for i, row in leaderboard.head(30).iterrows():
        addr = row["address"]
        username = row["username"]

        # Generate trades with skill correlated to rank
        skill = max(0.3, 1.0 - (i / 50))
        n_trades = random.randint(60, 350)
        trades = generate_wallet_trades(addr, n_trades=n_trades, skill_level=skill)
        storage.save_trades(addr, trades)

        result = analyze_wallet(addr, trades, leaderboard_pnl=row["pnl"])
        result["username"] = username
        result["volume"] = row["volume"]

        status = "✓ PASS" if result["passes_filters"] else "✗ FAIL"
        print(f"  [{i+1:3d}/30] {username[:15]:<15s} → score={result['score']:.3f}  "
              f"trades={result['total_trades']:4d}  wr={result['win_rate']:.1%}  {status}")

        results.append(result)

    scored = pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)
    storage.save_wallets(scored)

    passing = scored[scored["passes_filters"] == True].head(config.TOP_WALLETS_TO_ANALYZE)

    if not passing.empty:
        print(f"\n{'='*60}")
        print(f"  TOP {len(passing)} SCORED WALLETS")
        print(f"{'='*60}")
        display_cols = ["username", "score", "pnl", "total_trades", "win_rate",
                        "avg_entry_price", "max_drawdown", "consistency"]
        available = [c for c in display_cols if c in passing.columns]
        print(passing[available].to_string(index=False))

    # ─── Step 3: Extract patterns ───────────────────────────────────
    patterns = extract_patterns(storage, top_n=config.TOP_WALLETS_TO_ANALYZE)
    if patterns:
        print(f"\n{get_pattern_summary(patterns)}")

    # ─── Step 4: Generate and scan markets ──────────────────────────
    print(f"\n{'='*60}")
    print(f"  GENERATING DEMO MARKETS")
    print(f"{'='*60}")
    markets = generate_active_markets(n=200)
    storage.save_markets(markets)
    print(f"  ✓ Generated {len(markets)} active markets")

    matched = scan_markets(storage, patterns, refresh=False)

    if not matched.empty:
        strong = matched[matched["match_count"] >= config.ALERT_MIN_MATCH]
        for _, mkt in strong.head(10).iterrows():
            notifier.alert_market_match(
                market=mkt.to_dict(),
                score=mkt["match_score"],
                reasons=mkt["match_reasons"],
            )

    # ─── Step 5: Simulation ─────────────────────────────────────────
    if simulate:
        run_demo_simulation(storage, notifier, patterns)

    return scored, patterns, matched


def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Research & Simulation System"
    )
    parser.add_argument("--simulate", action="store_true",
                        help="Run paper trading simulation after research")
    parser.add_argument("--live", action="store_true",
                        help="Live trading mode (Phase 2 placeholder)")
    parser.add_argument("--quick", action="store_true",
                        help="Quick mode: fewer wallets, faster execution")
    parser.add_argument("--demo", action="store_true",
                        help="Demo mode: use synthetic data, no API calls needed")
    parser.add_argument("--db", type=str, default=config.DB_PATH,
                        help="Path to SQLite database")
    parser.add_argument("--skip-research", action="store_true",
                        help="Skip research, go straight to simulation (uses cached data)")
    parser.add_argument("--skip-dashboard", action="store_true",
                        help="Don't generate the HTML dashboard (used by run_live.py)")

    args = parser.parse_args()

    # ─── Live mode placeholder ──────────────────────────────────────
    if args.live:
        print_banner()
        print("\n" + "=" * 60)
        print("  🔴 LIVE MODE — Coming in Phase 2")
        print("=" * 60)
        print("""
  Live mode will:
    • Connect to Polymarket CLOB WebSocket for real-time prices
    • Monitor top wallet activity in real-time
    • Execute actual trades via authenticated API
    • Manage risk with automated stop-losses
    • Send alerts via Discord/Telegram

  For now, use --simulate for paper trading.
        """)
        return

    # ─── Initialize ─────────────────────────────────────────────────
    print_banner()

    storage = Storage(args.db)
    notifier = Notifier()

    start_time = time.time()

    # ─── Demo or Research Pipeline ──────────────────────────────────
    patterns = None
    if args.demo:
        scored, patterns, matched = run_demo(storage, notifier, simulate=args.simulate)
    elif not args.skip_research:
        scored, patterns, matched = run_research(storage, notifier, quick=args.quick)
        if args.simulate:
            run_forward_simulation(storage, notifier, patterns)
    else:
        print("\n  Skipping research, using cached data …")
        if args.simulate:
            run_forward_simulation(storage, notifier, patterns)

    # ─── Final Summary ──────────────────────────────────────────────
    elapsed = time.time() - start_time
    print_db_stats(storage)

    print(f"\n{'='*60}")
    print(f"  COMPLETE  ({elapsed:.1f}s)")
    print(f"{'='*60}")

    if notifier.get_alert_count() > 0:
        notifier.print_alert_summary()

    # ─── Generate Visual Dashboard ─────────────────────────────────
    if not args.skip_dashboard:
        dash_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
        try:
            generate_dashboard(
                db_path=args.db,
                output_path=dash_path,
                open_browser=True,
            )
        except Exception as e:
            print(f"\n  ⚠ Dashboard generation failed: {e}")

    storage.close()


if __name__ == "__main__":
    main()
