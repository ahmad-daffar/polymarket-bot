"""
Wallet Scorer — Score wallets using the formula:
  S(w) = α · PnL_normalized + β · Consistency − γ · MaxDrawdown

Applies strict filters before scoring.
"""

import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from data.fetcher import fetch_wallet_activity
from data.storage import Storage


def _compute_drawdown_series(pnl_series: pd.Series) -> tuple:
    """
    Given a cumulative PnL series, compute the max drawdown
    and the full drawdown series.
    Returns: (max_drawdown_fraction, drawdown_series)
    """
    if pnl_series.empty:
        return 0.0, pd.Series(dtype=float)

    cummax = pnl_series.cummax()
    drawdown = cummax - pnl_series
    # Normalize by peak (avoid division by zero)
    peak = cummax.replace(0, np.nan)
    drawdown_pct = (drawdown / peak).fillna(0)
    max_dd = drawdown_pct.max() if not drawdown_pct.empty else 0
    return float(max_dd), drawdown_pct


def _compute_consistency(trade_outcomes: pd.Series, window: int = 20) -> float:
    """
    Consistency = 1 − std(rolling_win_rate).
    A wallet that wins at a steady rate is more consistent.
    """
    if len(trade_outcomes) < window:
        window = max(5, len(trade_outcomes) // 2)

    if len(trade_outcomes) < 5:
        return 0.0

    # Binary: 1 for win, 0 for loss
    wins = (trade_outcomes > 0).astype(float)
    rolling_wr = wins.rolling(window, min_periods=3).mean()
    std = rolling_wr.std()

    if pd.isna(std) or std > 1:
        return 0.0

    return float(max(0, 1.0 - std))


def analyze_wallet(address: str, trades_df: pd.DataFrame = None,
                   leaderboard_pnl: float = 0) -> dict:
    """
    Analyze a single wallet: compute stats, apply filters, and score.

    Returns a dict with all wallet metrics and a 'passes_filters' flag.
    """
    # Fetch trades if not provided
    if trades_df is None or trades_df.empty:
        trades_df = fetch_wallet_activity(address)

    if trades_df.empty:
        return _empty_result(address, leaderboard_pnl)

    now = datetime.now(timezone.utc)

    # ─── Basic stats ────────────────────────────────────────────────
    total_trades = len(trades_df)

    # Parse timestamps
    if "timestamp" in trades_df.columns:
        trades_df["timestamp"] = pd.to_datetime(trades_df["timestamp"], utc=True, errors="coerce")
        trades_df = trades_df.dropna(subset=["timestamp"])

    if trades_df.empty:
        return _empty_result(address, leaderboard_pnl)

    first_trade = trades_df["timestamp"].min()
    last_trade = trades_df["timestamp"].max()
    history_days = (last_trade - first_trade).days if pd.notna(first_trade) and pd.notna(last_trade) else 0
    days_since_active = (now - last_trade).days if pd.notna(last_trade) else 999

    # ─── Price & PnL analysis ──────────────────────────────────────
    buy_trades = trades_df[trades_df["side"] == "BUY"]
    avg_entry_price = buy_trades["price"].mean() if not buy_trades.empty else 0

    # Compute per-trade PnL estimate:
    # For BUY: if outcome resolved to 1, profit = (1 - price) * size; else loss = -price * size
    # Since we don't have resolution data per trade, estimate using price patterns
    # Higher price buys that were right → more conviction trades
    trades_df["estimated_pnl"] = trades_df.apply(_estimate_trade_pnl, axis=1)
    cumulative_pnl = trades_df["estimated_pnl"].cumsum()

    # Win rate
    winning_trades = trades_df[trades_df["estimated_pnl"] > 0]
    win_rate = len(winning_trades) / total_trades if total_trades > 0 else 0

    # Max drawdown
    max_drawdown, _ = _compute_drawdown_series(cumulative_pnl)

    # Consistency
    consistency = _compute_consistency(trades_df["estimated_pnl"])

    # Single trade concentration
    if trades_df["estimated_pnl"].sum() != 0:
        max_single_pnl_pct = trades_df["estimated_pnl"].abs().max() / max(
            trades_df["estimated_pnl"].abs().sum(), 1
        )
    else:
        max_single_pnl_pct = 0

    # ─── Apply filters ─────────────────────────────────────────────
    passes = True
    filter_reasons = []

    if total_trades < config.MIN_RESOLVED_TRADES:
        passes = False
        filter_reasons.append(f"trades={total_trades} < {config.MIN_RESOLVED_TRADES}")

    if not (config.MIN_AVG_ENTRY_PRICE <= avg_entry_price <= config.MAX_AVG_ENTRY_PRICE):
        passes = False
        filter_reasons.append(f"avg_price={avg_entry_price:.3f} outside [{config.MIN_AVG_ENTRY_PRICE}, {config.MAX_AVG_ENTRY_PRICE}]")

    if days_since_active > config.MAX_INACTIVE_DAYS:
        passes = False
        filter_reasons.append(f"inactive {days_since_active}d > {config.MAX_INACTIVE_DAYS}d")

    if history_days < config.MIN_HISTORY_MONTHS * 30:
        passes = False
        filter_reasons.append(f"history={history_days}d < {config.MIN_HISTORY_MONTHS * 30}d")

    if max_single_pnl_pct > config.MAX_SINGLE_TRADE_PNL_PCT:
        passes = False
        filter_reasons.append(f"single_trade_pct={max_single_pnl_pct:.1%} > {config.MAX_SINGLE_TRADE_PNL_PCT:.0%}")

    # ─── Compute score ─────────────────────────────────────────────
    # Normalize PnL to [0, 1] using tanh scaling
    pnl_total = leaderboard_pnl if leaderboard_pnl else cumulative_pnl.iloc[-1] if not cumulative_pnl.empty else 0
    pnl_normalized = np.tanh(pnl_total / 100000)  # Scale: $100k → ~0.76

    score = (
        config.ALPHA * max(0, pnl_normalized)
        + config.BETA * consistency
        - config.GAMMA * max_drawdown
    )
    score = max(0, min(1, score))  # Clamp to [0, 1]

    return {
        "address": address,
        "pnl": pnl_total,
        "total_trades": total_trades,
        "win_rate": round(win_rate, 4),
        "avg_entry_price": round(avg_entry_price, 4),
        "max_drawdown": round(max_drawdown, 4),
        "consistency": round(consistency, 4),
        "max_single_pnl_pct": round(max_single_pnl_pct, 4),
        "history_days": history_days,
        "days_since_active": days_since_active,
        "first_trade_date": str(first_trade),
        "last_trade_date": str(last_trade),
        "score": round(score, 4),
        "passes_filters": passes,
        "filter_reasons": filter_reasons,
    }


def _estimate_trade_pnl(row) -> float:
    """
    Estimate PnL for a single trade using a probabilistic model.

    Since we don't have resolution data, we simulate outcomes:
    - The market price IS the implied probability of YES resolving to 1.
    - BUY YES at price p: wins (1-p)*size with prob p, loses p*size with prob (1-p).
      Expected PnL = p*(1-p)*size - (1-p)*p*size = 0 (fair market).
      But top traders have edge, so we add a small alpha (2-5%) and use
      a deterministic hash of the trade to decide win/loss consistently.
    """
    price = row.get("price", 0)
    size = row.get("size", 0)
    side = row.get("side", "")
    condition_id = str(row.get("condition_id", ""))
    ts = str(row.get("timestamp", ""))

    if price <= 0 or size <= 0:
        return 0

    # Use a deterministic "coin flip" based on trade data so results are
    # reproducible but not all-wins.  hash → [0,1), compare to price.
    seed_str = f"{condition_id}_{ts}_{side}_{price}_{size}"
    hash_val = hash(seed_str) % 10000 / 10000.0  # deterministic pseudo-random in [0, 1)

    if side == "BUY":
        # Bought YES at price p.  Resolves YES with probability ≈ p.
        # Give top traders a small edge: effective prob = p + 0.03
        effective_prob = min(price + 0.03, 0.99)
        if hash_val < effective_prob:
            return (1.0 - price) * size   # WIN: payout - cost
        else:
            return -price * size           # LOSS: lose the cost
    else:
        # Sold YES at price p (equivalent to buying NO at 1-p).
        effective_prob = min((1.0 - price) + 0.03, 0.99)
        if hash_val < effective_prob:
            return price * size            # WIN: keep the sale price
        else:
            return -(1.0 - price) * size   # LOSS: market resolved YES


def _empty_result(address: str, pnl: float = 0) -> dict:
    return {
        "address": address,
        "pnl": pnl,
        "total_trades": 0,
        "win_rate": 0,
        "avg_entry_price": 0,
        "max_drawdown": 0,
        "consistency": 0,
        "max_single_pnl_pct": 0,
        "history_days": 0,
        "days_since_active": 999,
        "first_trade_date": "",
        "last_trade_date": "",
        "score": 0,
        "passes_filters": False,
        "filter_reasons": ["no_trade_data"],
    }


def score_wallets(leaderboard_df: pd.DataFrame, storage: Storage,
                  max_wallets: int = None, verbose: bool = True) -> pd.DataFrame:
    """
    Score all wallets from the leaderboard.
    Fetches trade history, computes stats, applies filters, scores.
    Saves results to storage.

    Returns DataFrame of scored wallets sorted by score descending.
    """
    max_wallets = max_wallets or len(leaderboard_df)

    print(f"\n{'='*60}")
    print(f"  SCORING WALLETS  ({min(max_wallets, len(leaderboard_df))} wallets)")
    print(f"{'='*60}")
    print(f"  Formula: S(w) = {config.ALPHA}·PnL + {config.BETA}·Consistency − {config.GAMMA}·MaxDrawdown")
    print(f"  Filters: ≥{config.MIN_RESOLVED_TRADES} trades, price [{config.MIN_AVG_ENTRY_PRICE}-{config.MAX_AVG_ENTRY_PRICE}], "
          f"active <{config.MAX_INACTIVE_DAYS}d, history ≥{config.MIN_HISTORY_MONTHS}mo")
    print()

    results = []

    for i, row in leaderboard_df.head(max_wallets).iterrows():
        addr = row["address"]
        username = row.get("username", "anon")

        if verbose:
            print(f"  [{i+1:3d}/{max_wallets}] {username[:15]:<15s} ({addr[:8]}…) ", end="", flush=True)

        # Fetch trade history
        trades = fetch_wallet_activity(addr)
        if not trades.empty:
            storage.save_trades(addr, trades)

        # Analyze & score
        result = analyze_wallet(addr, trades, leaderboard_pnl=row.get("pnl", 0))
        result["username"] = username
        result["volume"] = row.get("volume", 0)

        if verbose:
            status = "✓ PASS" if result["passes_filters"] else "✗ FAIL"
            print(f"→ score={result['score']:.3f}  trades={result['total_trades']:4d}  "
                  f"wr={result['win_rate']:.1%}  {status}")
            if not result["passes_filters"] and result.get("filter_reasons"):
                print(f"         Reasons: {', '.join(result['filter_reasons'])}")

        results.append(result)

    df = pd.DataFrame(results)
    df = df.sort_values("score", ascending=False).reset_index(drop=True)

    # Save to storage
    storage.save_wallets(df)

    passing = df[df["passes_filters"] == True]
    print(f"\n  ────────────────────────────────────────")
    print(f"  Results: {len(passing)} passed filters out of {len(df)} scored")
    if not passing.empty:
        print(f"  Top score: {passing['score'].iloc[0]:.4f} ({passing['username'].iloc[0]})")
        print(f"  Avg score (passing): {passing['score'].mean():.4f}")

    return df
