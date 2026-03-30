"""
Performance Tracker — Calculate and display simulation performance metrics.

Tracks:
  - Total return
  - Win rate
  - Max drawdown
  - Sharpe ratio
  - Profit factor
  - Per-trade statistics
"""

import numpy as np
import pandas as pd

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from data.storage import Storage


class PerformanceTracker:
    def __init__(self, storage: Storage):
        self.storage = storage

    def compute_metrics(self, verbose: bool = True) -> dict:
        """
        Compute comprehensive performance metrics from simulated trades.
        """
        trades = self.storage.get_simulated_trades()

        if trades.empty:
            if verbose:
                print("\n  No simulated trades to analyze.")
            return self._empty_metrics()

        # Convert types
        trades["pnl"] = pd.to_numeric(trades["pnl"], errors="coerce")
        trades["entry_price"] = pd.to_numeric(trades["entry_price"], errors="coerce")
        trades["size"] = pd.to_numeric(trades["size"], errors="coerce")
        trades["bankroll_after"] = pd.to_numeric(trades["bankroll_after"], errors="coerce")
        trades["resolved"] = trades["resolved"].astype(int)

        total_trades = len(trades)
        resolved_trades = trades[trades["resolved"] == 1]
        open_trades = trades[trades["resolved"] == 0]

        # ─── Basic Stats ───────────────────────────────────────────
        total_invested = trades["size"].sum()
        resolved_pnl = resolved_trades["pnl"].sum() if not resolved_trades.empty else 0
        num_wins = len(resolved_trades[resolved_trades["pnl"] > 0]) if not resolved_trades.empty else 0
        num_losses = len(resolved_trades[resolved_trades["pnl"] <= 0]) if not resolved_trades.empty else 0
        win_rate = num_wins / len(resolved_trades) if len(resolved_trades) > 0 else 0

        # ─── Return ────────────────────────────────────────────────
        current_bankroll = trades["bankroll_after"].iloc[-1] if not trades.empty else config.STARTING_BANKROLL
        total_return = (current_bankroll - config.STARTING_BANKROLL) / config.STARTING_BANKROLL

        # ─── Drawdown ──────────────────────────────────────────────
        if not resolved_trades.empty:
            equity_curve = resolved_trades["bankroll_after"].values
            peak = np.maximum.accumulate(equity_curve)
            drawdown = (peak - equity_curve) / peak
            max_drawdown = float(np.max(drawdown)) if len(drawdown) > 0 else 0
        else:
            max_drawdown = 0
            equity_curve = np.array([config.STARTING_BANKROLL])

        # ─── Sharpe Ratio ──────────────────────────────────────────
        if not resolved_trades.empty and len(resolved_trades) > 1:
            returns = resolved_trades["pnl"] / resolved_trades["size"]
            returns = returns.replace([np.inf, -np.inf], np.nan).dropna()
            if returns.std() > 0:
                sharpe = float(returns.mean() / returns.std() * np.sqrt(252))
            else:
                sharpe = 0
        else:
            sharpe = 0

        # ─── Profit Factor ─────────────────────────────────────────
        if not resolved_trades.empty:
            gross_profit = resolved_trades[resolved_trades["pnl"] > 0]["pnl"].sum()
            gross_loss = abs(resolved_trades[resolved_trades["pnl"] <= 0]["pnl"].sum())
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        else:
            gross_profit = 0
            gross_loss = 0
            profit_factor = 0

        # ─── Avg Win / Loss ────────────────────────────────────────
        avg_win = resolved_trades[resolved_trades["pnl"] > 0]["pnl"].mean() if num_wins > 0 else 0
        avg_loss = resolved_trades[resolved_trades["pnl"] <= 0]["pnl"].mean() if num_losses > 0 else 0

        # ─── Expectancy ────────────────────────────────────────────
        expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss) if len(resolved_trades) > 0 else 0

        metrics = {
            "total_trades": total_trades,
            "resolved_trades": len(resolved_trades),
            "open_trades": len(open_trades),
            "wins": num_wins,
            "losses": num_losses,
            "win_rate": round(win_rate, 4),
            "total_pnl": round(resolved_pnl, 2),
            "total_return": round(total_return, 4),
            "current_bankroll": round(current_bankroll, 2),
            "max_drawdown": round(max_drawdown, 4),
            "sharpe_ratio": round(sharpe, 3),
            "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else "∞",
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "expectancy": round(expectancy, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "total_invested": round(total_invested, 2),
            "avg_position_size": round(trades["size"].mean(), 2),
        }

        if verbose:
            self._print_report(metrics)

        return metrics

    def _print_report(self, m: dict):
        """Print a formatted performance report."""
        print(f"\n{'='*60}")
        print(f"  SIMULATION PERFORMANCE REPORT")
        print(f"{'='*60}")

        # Portfolio
        print(f"\n  ── Portfolio ─────────────────────────────────────────")
        print(f"     Starting bankroll:  ${config.STARTING_BANKROLL:>10,.2f}")
        print(f"     Current bankroll:   ${m['current_bankroll']:>10,.2f}")
        pnl_color = "+" if m["total_pnl"] >= 0 else ""
        print(f"     Total P&L:          ${pnl_color}{m['total_pnl']:>9,.2f} ({m['total_return']:+.2%})")
        print(f"     Max drawdown:        {m['max_drawdown']:.2%}")

        # Trade Stats
        print(f"\n  ── Trade Statistics ──────────────────────────────────")
        print(f"     Total trades:     {m['total_trades']:>6d}")
        print(f"     Resolved:         {m['resolved_trades']:>6d}")
        print(f"     Open:             {m['open_trades']:>6d}")
        print(f"     Wins:             {m['wins']:>6d}")
        print(f"     Losses:           {m['losses']:>6d}")
        print(f"     Win rate:          {m['win_rate']:.1%}")

        # Risk Metrics
        print(f"\n  ── Risk Metrics ─────────────────────────────────────")
        print(f"     Sharpe ratio:      {m['sharpe_ratio']:>8.3f}")
        print(f"     Profit factor:     {m['profit_factor']}")
        print(f"     Avg win:          ${m['avg_win']:>10,.2f}")
        print(f"     Avg loss:         ${m['avg_loss']:>10,.2f}")
        print(f"     Expectancy:       ${m['expectancy']:>10,.2f} / trade")

        # Position Sizing
        print(f"\n  ── Sizing ───────────────────────────────────────────")
        print(f"     Total invested:   ${m['total_invested']:>10,.2f}")
        print(f"     Avg position:     ${m['avg_position_size']:>10,.2f}")
        print()

    def _empty_metrics(self) -> dict:
        return {
            "total_trades": 0, "resolved_trades": 0, "open_trades": 0,
            "wins": 0, "losses": 0, "win_rate": 0,
            "total_pnl": 0, "total_return": 0,
            "current_bankroll": config.STARTING_BANKROLL,
            "max_drawdown": 0, "sharpe_ratio": 0, "profit_factor": 0,
            "avg_win": 0, "avg_loss": 0, "expectancy": 0,
            "gross_profit": 0, "gross_loss": 0,
            "total_invested": 0, "avg_position_size": 0,
        }

    def get_equity_curve(self) -> pd.DataFrame:
        """Return equity curve data for plotting."""
        trades = self.storage.get_simulated_trades()
        if trades.empty:
            return pd.DataFrame({"trade_num": [0], "bankroll": [config.STARTING_BANKROLL]})

        trades["bankroll_after"] = pd.to_numeric(trades["bankroll_after"], errors="coerce")
        curve = trades[["timestamp", "bankroll_after"]].copy()
        curve.columns = ["timestamp", "bankroll"]
        curve = curve.reset_index(drop=True)
        curve.index.name = "trade_num"
        return curve

    def get_trade_details(self) -> pd.DataFrame:
        """Return detailed trade log."""
        trades = self.storage.get_simulated_trades()
        if trades.empty:
            return pd.DataFrame()

        return trades[[
            "timestamp", "wallet_source", "market_title", "side",
            "entry_price", "size", "exit_price", "pnl",
            "bankroll_after", "resolved"
        ]]
