"""
Notifier — Alert system for pattern-matched setups and simulation events.

Currently prints/logs alerts. Designed for future extension to
Discord webhooks, Telegram bots, email, etc.
"""

import json
from datetime import datetime, timezone

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config


class Notifier:
    def __init__(self, log_file: str = None):
        if log_file is None:
            # Pick a writable location for the log
            import tempfile
            candidates = [
                os.path.join(os.path.expanduser("~"), "polymarket_alerts.log"),
                os.path.join(tempfile.gettempdir(), "polymarket_alerts.log"),
            ]
            self.log_file = candidates[0]
            for path in candidates:
                try:
                    with open(path, "a") as f:
                        pass
                    self.log_file = path
                    break
                except (OSError, IOError):
                    continue
        else:
            self.log_file = log_file
        self.alert_history = []

    def _log(self, message: str):
        """Append to log file and print."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        entry = f"[{timestamp}] {message}"
        self.alert_history.append(entry)

        try:
            with open(self.log_file, "a") as f:
                f.write(entry + "\n")
        except (OSError, IOError):
            pass  # Log to memory only if file write fails

    # ─── Market Alerts ──────────────────────────────────────────────

    def alert_market_match(self, market: dict, score: float, reasons: list):
        """Alert when a live market matches the trading pattern."""
        title = market.get("title", "Unknown Market")[:60]
        prices = market.get("outcome_prices", [])
        prices_str = ", ".join(f"{p:.2f}" for p in prices[:2]) if prices else "N/A"

        msg = (
            f"\n  🎯 MARKET MATCH ALERT"
            f"\n  ├─ Market:  {title}"
            f"\n  ├─ Prices:  [{prices_str}]"
            f"\n  ├─ Score:   {score:.3f}"
            f"\n  ├─ Volume:  ${market.get('volume', 0):,.0f}"
            f"\n  ├─ Reasons: {' | '.join(reasons)}"
            f"\n  └─ Action:  Review for paper trade"
        )

        print(msg)
        self._log(f"MARKET_MATCH | {title} | score={score:.3f} | {', '.join(reasons)}")

    # ─── Wallet Alerts ──────────────────────────────────────────────

    def alert_wallet_trade(self, wallet_address: str, username: str,
                            trade: dict, wallet_score: float):
        """Alert when a top-scored wallet places a new trade."""
        msg = (
            f"\n  👛 TOP WALLET TRADE"
            f"\n  ├─ Wallet:  {username} ({wallet_address[:10]}…)"
            f"\n  ├─ Score:   {wallet_score:.3f}"
            f"\n  ├─ Action:  {trade.get('side', '')} @ {trade.get('price', 0):.2f}"
            f"\n  ├─ Size:    ${trade.get('size', 0):,.2f}"
            f"\n  ├─ Market:  {trade.get('market_title', '')[:50]}"
            f"\n  └─ Signal:  Copy candidate"
        )

        print(msg)
        self._log(f"WALLET_TRADE | {username} | {trade.get('side', '')} @ {trade.get('price', 0):.2f}")

    # ─── Simulation Alerts ──────────────────────────────────────────

    def alert_sim_trade(self, trade: dict, bankroll: float):
        """Alert when a simulated trade is executed."""
        msg = (
            f"    📊 SIM TRADE EXECUTED"
            f"\n       {trade.get('side', '')} {trade.get('market_title', '')[:40]}…"
            f"\n       Entry: {trade.get('entry_price', 0):.2f}  |  "
            f"Size: ${trade.get('size', 0):,.2f}  |  "
            f"Bankroll: ${bankroll:,.2f}"
        )
        print(msg)
        self._log(f"SIM_TRADE | {trade.get('side', '')} @ {trade.get('entry_price', 0):.2f} | "
                  f"size=${trade.get('size', 0):.2f} | bankroll=${bankroll:.2f}")

    def alert_sim_resolution(self, result: dict):
        """Alert when a simulated trade resolves."""
        won = result.get("outcome_won", False)
        emoji = "✅" if won else "❌"
        msg = (
            f"    {emoji} SIM TRADE RESOLVED"
            f"\n       PnL: ${result.get('pnl', 0):+,.2f}  |  "
            f"Bankroll: ${result.get('bankroll_after', 0):,.2f}"
        )
        print(msg)
        self._log(f"SIM_RESOLVE | pnl=${result.get('pnl', 0):+.2f} | "
                  f"bankroll=${result.get('bankroll_after', 0):.2f}")

    # ─── Performance Alerts ─────────────────────────────────────────

    def alert_performance_milestone(self, metric: str, value: float, threshold: float):
        """Alert on performance milestones (drawdown warning, etc)."""
        msg = (
            f"\n  ⚠️  PERFORMANCE ALERT"
            f"\n  ├─ Metric:    {metric}"
            f"\n  ├─ Value:     {value:.2%}"
            f"\n  └─ Threshold: {threshold:.2%}"
        )
        print(msg)
        self._log(f"PERF_ALERT | {metric}={value:.4f} | threshold={threshold:.4f}")

    # ─── Summary ────────────────────────────────────────────────────

    def print_alert_summary(self):
        """Print summary of all alerts in this session."""
        if not self.alert_history:
            print("  No alerts generated this session.")
            return

        print(f"\n  Alert Log ({len(self.alert_history)} alerts):")
        for entry in self.alert_history[-20:]:  # Last 20
            print(f"    {entry}")

    def get_alert_count(self) -> int:
        return len(self.alert_history)
