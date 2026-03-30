"""
Storage — SQLite persistence layer for all Polymarket bot data.
"""

import sqlite3
import json
import os
import pandas as pd
from datetime import datetime, timezone

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config


class Storage:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.DB_PATH
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        c = self.conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS wallets (
                address TEXT PRIMARY KEY,
                username TEXT,
                pnl REAL DEFAULT 0,
                volume REAL DEFAULT 0,
                score REAL DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                avg_entry_price REAL DEFAULT 0,
                max_drawdown REAL DEFAULT 0,
                consistency REAL DEFAULT 0,
                first_trade_date TEXT,
                last_trade_date TEXT,
                passes_filters INTEGER DEFAULT 0,
                updated_at TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT,
                timestamp TEXT,
                condition_id TEXT,
                market_title TEXT,
                side TEXT,
                price REAL,
                size REAL,
                outcome TEXT,
                outcome_index INTEGER,
                slug TEXT,
                tx_hash TEXT,
                UNIQUE(wallet_address, tx_hash)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS markets (
                condition_id TEXT PRIMARY KEY,
                title TEXT,
                slug TEXT,
                volume REAL DEFAULT 0,
                liquidity REAL DEFAULT 0,
                outcome_prices TEXT,
                clob_token_ids TEXT,
                end_date TEXT,
                category TEXT,
                active INTEGER DEFAULT 1,
                updated_at TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS simulated_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                wallet_source TEXT,
                condition_id TEXT,
                market_title TEXT,
                side TEXT,
                entry_price REAL,
                size REAL,
                fees_paid REAL DEFAULT 0,
                outcome TEXT,
                exit_price REAL,
                pnl REAL,
                bankroll_after REAL,
                resolved INTEGER DEFAULT 0,
                resolution_date TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT,
                pattern_key TEXT,
                pattern_value TEXT,
                sample_size INTEGER,
                updated_at TEXT
            )
        """)

        # ── Forward-simulation state ─────────────────────────────────────────
        # wallet_state: tracks the last trade timestamp we've seen per wallet
        # and how many forward trades we've observed (for warm-up guardrail).
        c.execute("""
            CREATE TABLE IF NOT EXISTS wallet_state (
                wallet_address TEXT PRIMARY KEY,
                last_trade_ts  INTEGER DEFAULT 0,
                forward_trades INTEGER DEFAULT 0,
                updated_at     TEXT
            )
        """)

        # bot_state: single-row key/value store for global bot metadata.
        c.execute("""
            CREATE TABLE IF NOT EXISTS bot_state (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        self.conn.commit()

    # ─── Wallets ────────────────────────────────────────────────────────

    def save_wallets(self, df: pd.DataFrame):
        """Save wallet data from a DataFrame."""
        now = datetime.now(timezone.utc).isoformat()
        c = self.conn.cursor()
        for _, row in df.iterrows():
            c.execute("""
                INSERT OR REPLACE INTO wallets
                (address, username, pnl, volume, score, total_trades, win_rate,
                 avg_entry_price, max_drawdown, consistency, first_trade_date,
                 last_trade_date, passes_filters, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row.get("address", ""),
                row.get("username", "anon"),
                row.get("pnl", 0),
                row.get("volume", 0),
                row.get("score", 0),
                row.get("total_trades", 0),
                row.get("win_rate", 0),
                row.get("avg_entry_price", 0),
                row.get("max_drawdown", 0),
                row.get("consistency", 0),
                str(row.get("first_trade_date", "")),
                str(row.get("last_trade_date", "")),
                int(row.get("passes_filters", 0)),
                now,
            ))
        self.conn.commit()

    def get_top_wallets(self, limit: int = 20) -> pd.DataFrame:
        """Get top wallets by score that pass filters."""
        query = """
            SELECT * FROM wallets
            WHERE passes_filters = 1
            ORDER BY score DESC
            LIMIT ?
        """
        return pd.read_sql_query(query, self.conn, params=(limit,))

    def get_all_wallets(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM wallets ORDER BY score DESC", self.conn)

    # ─── Trades ─────────────────────────────────────────────────────────

    def save_trades(self, wallet_address: str, df: pd.DataFrame):
        """Save trade history for a wallet."""
        c = self.conn.cursor()
        saved = 0
        for _, row in df.iterrows():
            try:
                c.execute("""
                    INSERT OR IGNORE INTO trades
                    (wallet_address, timestamp, condition_id, market_title, side,
                     price, size, outcome, outcome_index, slug, tx_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    wallet_address,
                    str(row.get("timestamp", "")),
                    row.get("condition_id", ""),
                    row.get("market_title", ""),
                    row.get("side", ""),
                    row.get("price", 0),
                    row.get("size", 0),
                    row.get("outcome", ""),
                    row.get("outcome_index"),
                    row.get("slug", ""),
                    row.get("tx_hash", ""),
                ))
                saved += 1
            except sqlite3.IntegrityError:
                pass
        self.conn.commit()
        return saved

    def get_wallet_trades(self, address: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT * FROM trades WHERE wallet_address = ? ORDER BY timestamp",
            self.conn, params=(address,)
        )

    def get_all_trades(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM trades ORDER BY timestamp", self.conn)

    # ─── Markets ────────────────────────────────────────────────────────

    def save_markets(self, df: pd.DataFrame):
        """Save market data."""
        now = datetime.now(timezone.utc).isoformat()
        c = self.conn.cursor()
        for _, row in df.iterrows():
            c.execute("""
                INSERT OR REPLACE INTO markets
                (condition_id, title, slug, volume, liquidity, outcome_prices,
                 clob_token_ids, end_date, category, active, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row.get("condition_id", ""),
                row.get("title", ""),
                row.get("slug", ""),
                row.get("volume", 0),
                row.get("liquidity", 0),
                json.dumps(row.get("outcome_prices", [])),
                json.dumps(row.get("clob_token_ids", [])),
                str(row.get("end_date", "")),
                row.get("category", "uncategorized"),
                int(row.get("active", True)),
                now,
            ))
        self.conn.commit()

    def get_active_markets(self) -> pd.DataFrame:
        df = pd.read_sql_query(
            "SELECT * FROM markets WHERE active = 1 ORDER BY volume DESC",
            self.conn
        )
        # Parse JSON columns back
        for col in ["outcome_prices", "clob_token_ids"]:
            if col in df.columns:
                df[col] = df[col].apply(lambda x: json.loads(x) if isinstance(x, str) else x)
        return df

    # ─── Simulated Trades ───────────────────────────────────────────────

    def save_simulated_trade(self, trade: dict):
        c = self.conn.cursor()
        c.execute("""
            INSERT INTO simulated_trades
            (timestamp, wallet_source, condition_id, market_title, side,
             entry_price, size, fees_paid, outcome, exit_price, pnl,
             bankroll_after, resolved, resolution_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade.get("timestamp", datetime.now(timezone.utc).isoformat()),
            trade.get("wallet_source", ""),
            trade.get("condition_id", ""),
            trade.get("market_title", ""),
            trade.get("side", ""),
            trade.get("entry_price", 0),
            trade.get("size", 0),
            trade.get("fees_paid", 0),
            trade.get("outcome", ""),
            trade.get("exit_price"),
            trade.get("pnl"),
            trade.get("bankroll_after"),
            int(trade.get("resolved", False)),
            trade.get("resolution_date"),
        ))
        self.conn.commit()
        return c.lastrowid

    def get_simulated_trades(self) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT * FROM simulated_trades ORDER BY timestamp",
            self.conn
        )

    def update_simulated_trade(self, trade_id: int, exit_price: float, pnl: float,
                                bankroll_after: float, resolution_date: str = None):
        c = self.conn.cursor()
        c.execute("""
            UPDATE simulated_trades
            SET exit_price = ?, pnl = ?, bankroll_after = ?, resolved = 1,
                resolution_date = ?
            WHERE id = ?
        """, (exit_price, pnl, bankroll_after, resolution_date, trade_id))
        self.conn.commit()

    # ─── Patterns ───────────────────────────────────────────────────────

    def save_pattern(self, pattern_type: str, key: str, value, sample_size: int):
        now = datetime.now(timezone.utc).isoformat()
        c = self.conn.cursor()
        c.execute("""
            INSERT OR REPLACE INTO patterns
            (pattern_type, pattern_key, pattern_value, sample_size, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (pattern_type, key, json.dumps(value), sample_size, now))
        self.conn.commit()

    def get_patterns(self, pattern_type: str = None) -> pd.DataFrame:
        if pattern_type:
            df = pd.read_sql_query(
                "SELECT * FROM patterns WHERE pattern_type = ?",
                self.conn, params=(pattern_type,)
            )
        else:
            df = pd.read_sql_query("SELECT * FROM patterns", self.conn)
        if "pattern_value" in df.columns:
            df["pattern_value"] = df["pattern_value"].apply(
                lambda x: json.loads(x) if isinstance(x, str) else x
            )
        return df

    # ─── Bot State (key/value) ───────────────────────────────────────────

    def get_state(self, key: str, default=None):
        c = self.conn.cursor()
        row = c.execute("SELECT value FROM bot_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_state(self, key: str, value):
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES (?,?)",
                  (key, str(value)))
        self.conn.commit()

    # ─── Wallet Forward State ────────────────────────────────────────────

    def get_wallet_last_ts(self, wallet_address: str) -> int:
        """Return the last trade timestamp (unix seconds) seen for this wallet."""
        c = self.conn.cursor()
        row = c.execute(
            "SELECT last_trade_ts FROM wallet_state WHERE wallet_address=?",
            (wallet_address,)
        ).fetchone()
        return int(row[0]) if row else 0

    def update_wallet_state(self, wallet_address: str,
                             last_trade_ts: int, forward_trades_delta: int = 0):
        """Upsert the wallet's forward state."""
        now = datetime.now(timezone.utc).isoformat()
        c = self.conn.cursor()
        c.execute("""
            INSERT INTO wallet_state (wallet_address, last_trade_ts, forward_trades, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(wallet_address) DO UPDATE SET
                last_trade_ts  = MAX(last_trade_ts, excluded.last_trade_ts),
                forward_trades = forward_trades + ?,
                updated_at     = excluded.updated_at
        """, (wallet_address, last_trade_ts, 0, now, forward_trades_delta))
        self.conn.commit()

    def get_wallet_forward_trades(self, wallet_address: str) -> int:
        c = self.conn.cursor()
        row = c.execute(
            "SELECT forward_trades FROM wallet_state WHERE wallet_address=?",
            (wallet_address,)
        ).fetchone()
        return int(row[0]) if row else 0

    # ─── Utilities ──────────────────────────────────────────────────────

    def close(self):
        self.conn.close()

    def stats(self) -> dict:
        c = self.conn.cursor()
        return {
            "wallets": c.execute("SELECT COUNT(*) FROM wallets").fetchone()[0],
            "wallets_passing": c.execute("SELECT COUNT(*) FROM wallets WHERE passes_filters=1").fetchone()[0],
            "trades": c.execute("SELECT COUNT(*) FROM trades").fetchone()[0],
            "markets": c.execute("SELECT COUNT(*) FROM markets WHERE active=1").fetchone()[0],
            "simulated_trades": c.execute("SELECT COUNT(*) FROM simulated_trades").fetchone()[0],
            "patterns": c.execute("SELECT COUNT(*) FROM patterns").fetchone()[0],
        }
