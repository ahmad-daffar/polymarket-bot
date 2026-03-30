"""
Paper Trader — Simulate trades with virtual money by copying top wallet activity.

Uses fractional Kelly criterion for position sizing:
  f = min(bankroll * kelly_fraction, max_position)

Trades are triggered when a top wallet places a new bet matching the pattern.
"""

import json
import numpy as np
import pandas as pd
from datetime import datetime, timezone

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from data.storage import Storage
from data.fetcher import fetch_wallet_activity, fetch_market_price


class PaperTrader:
    def __init__(self, storage: Storage, bankroll: float = None):
        self.storage = storage
        self.bankroll = bankroll or config.STARTING_BANKROLL
        self.initial_bankroll = self.bankroll
        self.positions = {}       # condition_id -> position dict (currently open)
        self.traded_ids = set()   # all condition_ids ever traded (prevents re-trading same market)
        self.trade_log = []
        self.peak_bankroll = self.bankroll

        # Restore state from DB if available
        self._restore_state()

    def _restore_state(self):
        """Restore bankroll and open positions from previous sim trades."""
        sim_trades = self.storage.get_simulated_trades()
        if sim_trades.empty:
            return

        # Get the latest bankroll
        resolved = sim_trades[sim_trades["resolved"] == 1]
        if not resolved.empty:
            self.bankroll = float(resolved.iloc[-1]["bankroll_after"])
            self.peak_bankroll = max(self.peak_bankroll, self.bankroll)

        # Restore all previously traded condition IDs (open + resolved)
        for _, trade in sim_trades.iterrows():
            cid = trade.get("condition_id", "")
            if cid:
                self.traded_ids.add(cid)

        # Restore only open positions to positions dict
        open_trades = sim_trades[sim_trades["resolved"] == 0]
        for _, trade in open_trades.iterrows():
            cid = trade["condition_id"]
            self.positions[cid] = {
                "trade_id": trade["id"],
                "condition_id": cid,
                "market_title": trade["market_title"],
                "side": trade["side"],
                "entry_price": float(trade["entry_price"]),
                "size": float(trade["size"]),
                "wallet_source": trade["wallet_source"],
                "timestamp": trade["timestamp"],
            }

        print(f"  Restored state: bankroll=${self.bankroll:,.2f}, "
              f"{len(self.positions)} open positions, {len(self.traded_ids)} prior trades")

    def compute_kelly_size(self, edge: float, odds: float) -> float:
        """
        Fractional Kelly criterion.
        edge: estimated probability advantage (p - market_price)
        odds: net odds (payout / stake - 1)
        Returns: position size in dollars.

        NOTE: We size off the INITIAL bankroll to prevent runaway compounding
        during backtests. This gives realistic fixed-dollar position sizes.
        """
        if edge <= config.MIN_EDGE or odds <= 0:
            return 0

        # Kelly fraction: f* = edge / odds
        kelly_full = min(edge / odds, 0.25)   # cap kelly at 25% before fraction
        kelly_frac = kelly_full * config.KELLY_FRACTION  # Quarter-Kelly

        # Size off initial bankroll for stable backtesting
        size = self.initial_bankroll * kelly_frac
        max_size = self.initial_bankroll * config.MAX_POSITION_PCT

        return max(0, min(size, max_size))

    def evaluate_trade(self, wallet_address: str, trade: dict,
                       patterns: dict = None) -> dict:
        """
        Evaluate whether to copy a trade from a top wallet.
        Returns: {"take": bool, "size": float, "reason": str}
        """
        price = float(trade.get("price", 0))
        side = trade.get("side", "")
        condition_id = trade.get("condition_id", "")

        # Skip if already traded this market (open or previously resolved)
        if condition_id in self.traded_ids:
            return {"take": False, "size": 0, "reason": "already_traded"}

        # Skip if price outside pattern range
        if not (config.MIN_AVG_ENTRY_PRICE <= price <= config.MAX_AVG_ENTRY_PRICE):
            return {"take": False, "size": 0, "reason": f"price {price:.2f} outside range"}

        # Estimate edge: how far is the price from the pattern's sweet spot?
        pattern_mean = 0.40
        if patterns and "price_ranges" in patterns:
            pattern_mean = patterns["price_ranges"].get("mean", 0.40)

        # For a BUY at price p, if the "true" probability is higher than p, we have edge
        # Use pattern mean as our estimate of the typical value zone center
        if side == "BUY":
            # Estimated edge: the wallet thinks true prob > price
            # We estimate edge proportional to how much price is below our sweet spot
            estimated_prob = min(0.85, price + 0.15)  # Generous estimate
            edge = estimated_prob - price
            odds = (1.0 / price) - 1  # Net odds
        else:
            # SELL: edge = price - estimated_true_prob
            estimated_prob = max(0.15, price - 0.15)
            edge = price - estimated_prob
            odds = (1.0 / (1 - price)) - 1

        size = self.compute_kelly_size(edge, odds)

        if size < 1.0:  # Minimum $1 trade
            return {"take": False, "size": 0, "reason": f"kelly_size too small (${size:.2f})"}

        return {
            "take": True,
            "size": round(size, 2),
            "edge": round(edge, 4),
            "odds": round(odds, 4),
            "reason": "pattern_match",
        }

    def execute_paper_trade(self, wallet_address: str, trade_data: dict,
                             eval_result: dict) -> dict:
        """
        Execute a simulated trade. Deducts from bankroll, logs to storage.
        """
        size = eval_result["size"]
        price = float(trade_data.get("price", 0))
        condition_id = trade_data.get("condition_id", "")

        # Entry fee (taker fee on notional)
        entry_fee = size * config.TAKER_FEE_PCT

        # Deduct size + entry fee from bankroll
        self.bankroll -= (size + entry_fee)

        trade_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "wallet_source": wallet_address,
            "condition_id": condition_id,
            "market_title": trade_data.get("market_title", ""),
            "side": trade_data.get("side", ""),
            "entry_price": price,
            "size": size,
            "fees_paid": round(entry_fee, 4),
            "outcome": "",
            "exit_price": None,
            "pnl": None,
            "bankroll_after": self.bankroll,
            "resolved": False,
            "resolution_date": None,
        }

        trade_id = self.storage.save_simulated_trade(trade_record)

        # Track open position and prevent re-trading this market
        self.traded_ids.add(condition_id)
        self.positions[condition_id] = {
            "trade_id": trade_id,
            **trade_record,
        }

        self.trade_log.append(trade_record)
        return trade_record

    def resolve_trade(self, condition_id: str, outcome_won: bool) -> dict:
        """
        Resolve an open simulated trade.
        outcome_won: True if the side we bet on was correct.
        """
        if condition_id not in self.positions:
            return None

        pos = self.positions.pop(condition_id)
        entry_price = pos["entry_price"]
        size = pos["size"]

        if outcome_won:
            # Gross profit = size * (1/price - 1)
            gross_profit = size * ((1.0 / entry_price) - 1)
            # Exit fee on gross profit
            exit_fee = gross_profit * config.WINNER_FEE_PCT
            pnl = gross_profit - exit_fee
        else:
            pnl = -size
            exit_fee = 0

        self.bankroll += size + pnl  # Return stake + net profit/loss
        self.peak_bankroll = max(self.peak_bankroll, self.bankroll)

        exit_price = 1.0 if outcome_won else 0.0

        self.storage.update_simulated_trade(
            trade_id=pos["trade_id"],
            exit_price=exit_price,
            pnl=pnl,
            bankroll_after=self.bankroll,
            resolution_date=datetime.now(timezone.utc).isoformat(),
        )

        return {
            "condition_id": condition_id,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "size": size,
            "pnl": round(pnl, 2),
            "outcome_won": outcome_won,
            "bankroll_after": round(self.bankroll, 2),
        }

    def simulate_from_wallet_history(self, wallet_address: str,
                                       trades_df: pd.DataFrame = None,
                                       patterns: dict = None,
                                       verbose: bool = True) -> list:
        """
        Run a backtest-style simulation by replaying a wallet's trade history.
        Evaluates each trade, executes a paper copy, then immediately resolves
        using a probabilistic outcome (since we're backtesting, not live).
        """
        if trades_df is None or trades_df.empty:
            trades_df = fetch_wallet_activity(wallet_address)

        if trades_df.empty:
            return []

        executed = []
        skipped = 0
        wins = 0
        losses = 0

        for _, trade in trades_df.iterrows():
            eval_result = self.evaluate_trade(wallet_address, trade.to_dict(), patterns)

            if eval_result["take"]:
                record = self.execute_paper_trade(
                    wallet_address, trade.to_dict(), eval_result
                )

                # ── Immediately resolve (backtest mode) ──────────────
                # Use deterministic hash to decide outcome, same approach
                # as the wallet scorer's PnL estimator.
                cid = trade.get("condition_id", "")
                price = float(trade.get("price", 0))
                side = trade.get("side", "")
                seed_str = f"sim_{cid}_{wallet_address}_{price}_{side}"
                hash_val = hash(seed_str) % 10000 / 10000.0

                # Top traders have ~3% edge over the market
                if side == "BUY":
                    win_prob = min(price + 0.03, 0.95)
                else:
                    win_prob = min((1.0 - price) + 0.03, 0.95)

                outcome_won = hash_val < win_prob
                resolution = self.resolve_trade(cid, outcome_won)

                if resolution:
                    record["resolved"] = True
                    record["pnl"] = resolution["pnl"]
                    record["bankroll_after"] = resolution["bankroll_after"]
                    if outcome_won:
                        wins += 1
                    else:
                        losses += 1

                executed.append(record)

                if verbose:
                    result_icon = "✅" if outcome_won else "❌"
                    pnl_str = f"${resolution['pnl']:+.2f}" if resolution else ""
                    print(f"    {result_icon} {trade.get('side', '')} "
                          f"{trade.get('market_title', '')[:40]}… "
                          f"@ {trade.get('price', 0):.2f} "
                          f"size=${eval_result['size']:.2f} → {pnl_str} "
                          f"(bank=${self.bankroll:,.2f})")
            else:
                skipped += 1

        if verbose:
            total = wins + losses
            wr = wins / total * 100 if total > 0 else 0
            print(f"\n    Executed: {len(executed)}  |  Skipped: {skipped}")
            print(f"    Wins: {wins}  |  Losses: {losses}  |  Win rate: {wr:.1f}%")

        return executed

    def get_portfolio_summary(self) -> dict:
        """Return current portfolio state."""
        return {
            "bankroll": round(self.bankroll, 2),
            "initial_bankroll": self.initial_bankroll,
            "total_return": round((self.bankroll - self.initial_bankroll) / self.initial_bankroll, 4),
            "open_positions": len(self.positions),
            "peak_bankroll": round(self.peak_bankroll, 2),
            "positions": list(self.positions.values()),
        }
