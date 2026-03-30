"""
Paper Trader — Forward-only simulation. Mirrors top wallet trades in real-time.

How it works:
  • Each 2-hour run fetches trades that happened SINCE THE LAST RUN per wallet.
  • Those are the only trades we act on. No history, no backtest.
  • Bankroll starts at INITIAL_BANKROLL and evolves purely from forward trades.
  • WARM-UP GUARDRAIL: until we have seen MIN_FORWARD_TRADES from a wallet,
    we use MICRO_BET_PCT (0.5% of bankroll) instead of Kelly — prevents a single
    early lucky/unlucky trade from skewing the whole run.
  • RESOLUTION: each run also checks all open positions. If the market's current
    price has collapsed to near 0 or near 1, we resolve the trade accordingly.
"""

import time
from datetime import datetime, timezone

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from data.storage import Storage
from data.fetcher import fetch_wallet_activity, fetch_market_price


class PaperTrader:
    def __init__(self, storage: Storage, bankroll: float = None):
        self.storage = storage
        self.initial_bankroll = bankroll or config.INITIAL_BANKROLL
        self.bankroll = self.initial_bankroll
        self.peak_bankroll = self.initial_bankroll
        self.positions = {}       # condition_id → position dict (open)
        self.traded_ids = set()   # all condition_ids ever entered
        self.trade_log = []

        # Restore persistent state from DB
        self._restore_state()

    # ─── State Restore ───────────────────────────────────────────────────────

    def _restore_state(self):
        """Pick up where the last run left off."""
        sim_trades = self.storage.get_simulated_trades()
        if sim_trades.empty:
            return

        # Current bankroll = last resolved trade's bankroll_after, or initial
        resolved = sim_trades[sim_trades["resolved"] == 1].sort_values("resolution_date")
        if not resolved.empty:
            self.bankroll = float(resolved.iloc[-1]["bankroll_after"])
            self.peak_bankroll = max(self.peak_bankroll, self.bankroll)

        # All condition_ids we've ever entered (prevents re-entry)
        for _, t in sim_trades.iterrows():
            cid = t.get("condition_id", "")
            if cid:
                self.traded_ids.add(cid)

        # Restore open positions
        open_trades = sim_trades[sim_trades["resolved"] == 0]
        for _, t in open_trades.iterrows():
            cid = t["condition_id"]
            self.positions[cid] = {
                "trade_id":     t["id"],
                "condition_id": cid,
                "market_title": t["market_title"],
                "side":         t["side"],
                "entry_price":  float(t["entry_price"]),
                "size":         float(t["size"]),
                "wallet_source": t["wallet_source"],
                "timestamp":    t["timestamp"],
            }

        print(f"  Restored: bankroll=${self.bankroll:,.2f} | "
              f"{len(self.positions)} open positions | {len(self.traded_ids)} lifetime trades")

    # ─── Position Sizing ─────────────────────────────────────────────────────

    def _compute_size(self, wallet_address: str, edge: float, odds: float) -> float:
        """
        Size a trade using fractional Kelly, but fall back to micro-bet during warm-up.
        warm-up = wallet has fewer than MIN_FORWARD_TRADES in the forward period.
        """
        forward_seen = self.storage.get_wallet_forward_trades(wallet_address)

        # Warm-up: tiny probe bet so we don't blow up on early noise
        if forward_seen < config.MIN_FORWARD_TRADES:
            micro = self.bankroll * config.MICRO_BET_PCT
            return round(max(1.0, min(micro, self.bankroll * 0.01)), 2)

        # Full Kelly (capped at 25% of bankroll, then quarter-Kelly applied)
        if edge <= config.MIN_EDGE or odds <= 0:
            return 0
        kelly_full = min(edge / odds, 0.25)
        kelly_frac = kelly_full * config.KELLY_FRACTION
        size = self.initial_bankroll * kelly_frac
        max_size = self.initial_bankroll * config.MAX_POSITION_PCT
        return round(max(0, min(size, max_size)), 2)

    # ─── Trade Evaluation ────────────────────────────────────────────────────

    def evaluate_trade(self, wallet_address: str, trade: dict,
                       patterns: dict = None) -> dict:
        """Should we copy this trade? Returns {take, size, reason}."""
        price    = float(trade.get("price", 0))
        side     = trade.get("side", "")
        cond_id  = trade.get("condition_id", "")

        if cond_id in self.traded_ids:
            return {"take": False, "size": 0, "reason": "already_traded"}

        if not (config.MIN_AVG_ENTRY_PRICE <= price <= config.MAX_AVG_ENTRY_PRICE):
            return {"take": False, "size": 0, "reason": f"price {price:.2f} out of range"}

        # Edge estimate: top wallet's edge over market price ≈ 3–15%
        if side == "BUY":
            estimated_prob = min(0.85, price + 0.15)
            edge = estimated_prob - price
            odds = (1.0 / price) - 1
        else:
            estimated_prob = max(0.15, price - 0.15)
            edge = price - estimated_prob
            odds = (1.0 / (1 - price)) - 1

        size = self._compute_size(wallet_address, edge, odds)
        if size < 1.0:
            return {"take": False, "size": 0, "reason": f"size too small (${size:.2f})"}

        return {
            "take":   True,
            "size":   size,
            "edge":   round(edge, 4),
            "odds":   round(odds, 4),
            "reason": "forward_signal",
            "warm_up": self.storage.get_wallet_forward_trades(wallet_address) < config.MIN_FORWARD_TRADES,
        }

    # ─── Trade Execution ─────────────────────────────────────────────────────

    def execute_paper_trade(self, wallet_address: str, trade_data: dict,
                             eval_result: dict) -> dict:
        """Enter a simulated position. Deducts stake + entry fee from bankroll."""
        size      = eval_result["size"]
        price     = float(trade_data.get("price", 0))
        cond_id   = trade_data.get("condition_id", "")
        entry_fee = size * config.TAKER_FEE_PCT

        self.bankroll -= (size + entry_fee)

        record = {
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "wallet_source": wallet_address,
            "condition_id":  cond_id,
            "market_title":  trade_data.get("market_title", ""),
            "side":          trade_data.get("side", ""),
            "entry_price":   price,
            "size":          size,
            "fees_paid":     round(entry_fee, 4),
            "outcome":       "",
            "exit_price":    None,
            "pnl":           None,
            "bankroll_after": self.bankroll,
            "resolved":      False,
            "resolution_date": None,
        }

        trade_id = self.storage.save_simulated_trade(record)
        self.traded_ids.add(cond_id)
        self.positions[cond_id] = {"trade_id": trade_id, **record}
        self.trade_log.append(record)
        return record

    # ─── Trade Resolution ────────────────────────────────────────────────────

    def resolve_trade(self, condition_id: str, outcome_won: bool) -> dict:
        """Close an open simulated position at market resolution."""
        if condition_id not in self.positions:
            return None
        pos = self.positions.pop(condition_id)
        entry_price = pos["entry_price"]
        size        = pos["size"]

        if outcome_won:
            gross_profit = size * ((1.0 / entry_price) - 1)
            exit_fee     = gross_profit * config.WINNER_FEE_PCT
            pnl          = gross_profit - exit_fee
        else:
            pnl      = -size
            exit_fee = 0

        self.bankroll     += size + pnl
        self.peak_bankroll = max(self.peak_bankroll, self.bankroll)
        exit_price         = 1.0 if outcome_won else 0.0

        self.storage.update_simulated_trade(
            trade_id        = pos["trade_id"],
            exit_price      = exit_price,
            pnl             = round(pnl, 4),
            bankroll_after  = round(self.bankroll, 4),
            resolution_date = datetime.now(timezone.utc).isoformat(),
        )
        return {
            "condition_id": condition_id,
            "entry_price":  entry_price,
            "exit_price":   exit_price,
            "size":         size,
            "pnl":          round(pnl, 2),
            "outcome_won":  outcome_won,
            "bankroll_after": round(self.bankroll, 2),
        }

    # ─── Forward Runner (called each 2-hour cycle) ───────────────────────────

    def run_forward_cycle(self, wallet_address: str, recent_trades: list,
                          patterns: dict = None, verbose: bool = True) -> list:
        """
        Process ONLY trades that arrived since the last run for this wallet.
        recent_trades: list of trade dicts already filtered to the new window.
        Returns list of executed trade records.
        """
        executed = []
        for trade in recent_trades:
            ev = self.evaluate_trade(wallet_address, trade, patterns)
            if ev["take"]:
                record = self.execute_paper_trade(wallet_address, trade, ev)
                executed.append(record)
                # Count this toward the wallet's forward trade tally
                self.storage.update_wallet_state(
                    wallet_address,
                    last_trade_ts       = int(time.time()),
                    forward_trades_delta = 1,
                )
                if verbose:
                    warm = " [warm-up]" if ev.get("warm_up") else ""
                    print(f"    ▶ {trade.get('side','')} {trade.get('market_title','')[:45]}…"
                          f" @ {trade.get('price',0):.2f}"
                          f"  size=${ev['size']:.2f}{warm}")
        return executed

    # ─── Open Position Resolution ────────────────────────────────────────────

    def check_and_resolve_open_positions(self, verbose: bool = True) -> list:
        """
        For every open position, fetch the current market price.
        If it has resolved (price ≤ 0.02 or ≥ 0.98), close the trade.
        Returns list of resolution results.
        """
        resolved = []
        for cond_id, pos in list(self.positions.items()):
            try:
                price_data = fetch_market_price(cond_id)
                current_price = price_data.get("mid") if isinstance(price_data, dict) else None
            except Exception:
                continue

            if not current_price:
                continue

            side = pos.get("side", "BUY")

            # Market resolved YES (price → 1)
            if current_price >= 0.98:
                outcome_won = (side == "BUY")
                result = self.resolve_trade(cond_id, outcome_won)
                if result:
                    resolved.append(result)
                    if verbose:
                        icon = "✅" if outcome_won else "❌"
                        print(f"    {icon} RESOLVED {pos.get('market_title','')[:40]}… "
                              f"→ YES  pnl=${result['pnl']:+.2f}  "
                              f"bank=${result['bankroll_after']:,.2f}")

            # Market resolved NO (price → 0)
            elif current_price <= 0.02:
                outcome_won = (side == "SELL")
                result = self.resolve_trade(cond_id, outcome_won)
                if result:
                    resolved.append(result)
                    if verbose:
                        icon = "✅" if outcome_won else "❌"
                        print(f"    {icon} RESOLVED {pos.get('market_title','')[:40]}… "
                              f"→ NO   pnl=${result['pnl']:+.2f}  "
                              f"bank=${result['bankroll_after']:,.2f}")

        return resolved

    # ─── Portfolio Summary ───────────────────────────────────────────────────

    def get_portfolio_summary(self) -> dict:
        return {
            "bankroll":       round(self.bankroll, 2),
            "initial_bankroll": self.initial_bankroll,
            "total_return":   round((self.bankroll - self.initial_bankroll) / self.initial_bankroll, 4),
            "open_positions": len(self.positions),
            "peak_bankroll":  round(self.peak_bankroll, 2),
            "positions":      list(self.positions.values()),
        }
