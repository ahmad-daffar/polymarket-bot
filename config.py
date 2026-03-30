"""
Polymarket Research & Simulation System — Configuration
"""

# ─── API Endpoints ───────────────────────────────────────────────────────────
GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# ─── Wallet Scoring Weights: S(w) = α·PnL + β·Consistency − γ·MaxDrawdown ──
ALPHA = 0.50   # PnL weight
BETA = 0.30    # Consistency weight (win‐rate stability)
GAMMA = 0.20   # Max drawdown penalty

# ─── Wallet Filters (strict) ────────────────────────────────────────────────
MIN_RESOLVED_TRADES = 80
MIN_AVG_ENTRY_PRICE = 0.20
MAX_AVG_ENTRY_PRICE = 0.70
MAX_INACTIVE_DAYS = 14
# NOTE: The /activity API only returns recent trades (not full history).
# Leaderboard PnL already proves these are established traders, so we
# relax the history requirement to work with available API data.
MIN_HISTORY_MONTHS = 0
MAX_SINGLE_TRADE_PNL_PCT = 0.30   # No single trade > 30% of total PnL

# ─── Simulation ─────────────────────────────────────────────────────────────
# Starting capital — this is the only number you need to change to control size.
# Set via env var INITIAL_BANKROLL or edit here.
import os as _os2
INITIAL_BANKROLL = float(_os2.environ.get("INITIAL_BANKROLL", "10000"))

STARTING_BANKROLL = INITIAL_BANKROLL   # alias kept for legacy references
KELLY_FRACTION = 0.25              # Quarter-Kelly for safety
MAX_POSITION_PCT = 0.10            # Max 10% of bankroll per trade
MIN_EDGE = 0.02                    # Minimum edge to take a trade

# ─── Forward-Only Paper Trading ──────────────────────────────────────────────
# The bot ONLY mirrors trades that happen AFTER it first ran. No backtest numbers.
# MIN_FORWARD_TRADES: we need to observe this many trades from a wallet in the
# FORWARD period before trusting its edge enough for full Kelly sizing.
# Until then we use MICRO_BET_PCT of bankroll (tiny probe bets).
MIN_FORWARD_TRADES = 5             # Trades needed before full Kelly kicks in
MICRO_BET_PCT = 0.005              # 0.5% bankroll per trade while warming up

# How old a trade can be (in seconds) to be considered "new" on this run.
# 2-hour runs → 7500s window (with buffer for latency).
NEW_TRADE_WINDOW_SECS = 7500

# ─── Transaction Fees (Polymarket CLOB) ─────────────────────────────────────
# Taker fee: 2% of notional on entry. No fee on losing trades (you just lose stake).
# Winning trades also pay 2% of gross profit on exit.
TAKER_FEE_PCT = 0.02              # 2% fee on trade entry (of size)
WINNER_FEE_PCT = 0.02             # 2% fee on gross profit when trade resolves YES

# ─── Data Fetching ──────────────────────────────────────────────────────────
LEADERBOARD_WINDOW = "all"         # 1d, 7d, 30d, all
LEADERBOARD_LIMIT = 100            # Max wallets to pull from leaderboard
TOP_WALLETS_TO_ANALYZE = 20        # How many pass scoring for pattern analysis
MARKET_FETCH_LIMIT = 100           # Markets per API page
ACTIVITY_FETCH_LIMIT = 500         # Activity records per page

# ─── Database ───────────────────────────────────────────────────────────────
import os as _os
import tempfile as _tempfile
# SQLite needs a writable filesystem — try project dir first, fall back to user home / temp
_project_db = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "polymarket_bot.db")
_home_db = _os.path.join(_os.path.expanduser("~"), "polymarket_bot.db")
_temp_db = _os.path.join(_tempfile.gettempdir(), "polymarket_bot.db")

def _pick_db_path():
    if _os.environ.get("POLYBOT_DB"):
        return _os.environ["POLYBOT_DB"]
    # Test if project directory is writable
    project_dir = _os.path.dirname(_os.path.abspath(__file__))
    try:
        test_file = _os.path.join(project_dir, ".write_test")
        with open(test_file, "w") as f:
            f.write("test")
        _os.remove(test_file)
        return _project_db
    except (OSError, IOError):
        pass
    # Fall back to home directory, then temp
    try:
        with open(_home_db + ".test", "w") as f:
            f.write("test")
        _os.remove(_home_db + ".test")
        return _home_db
    except (OSError, IOError):
        return _temp_db

DB_PATH = _pick_db_path()

# ─── Alerts ─────────────────────────────────────────────────────────────────
ALERT_MIN_SCORE = 0.60             # Minimum wallet score to trigger alert
ALERT_MIN_MATCH = 3                # Minimum pattern matches for market alert
