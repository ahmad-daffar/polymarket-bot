"""
Demo Data Generator — Creates realistic synthetic Polymarket data
for testing the full pipeline when the live API is unreachable.
"""

import random
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta


# Seed for reproducibility
random.seed(42)
np.random.seed(42)

CATEGORIES = [
    "politics", "sports", "crypto", "economics", "entertainment",
    "science", "tech", "world-affairs", "elections", "finance",
]

MARKET_TEMPLATES = [
    ("Will {entity} win the {event}?", "politics"),
    ("Will {crypto} reach ${price}K by {date}?", "crypto"),
    ("Will {team} win the {league} championship?", "sports"),
    ("Will the Fed {action} rates in {month}?", "economics"),
    ("Will {company} stock hit ${sprice} by {date}?", "finance"),
    ("Will {entity} announce {thing} before {date}?", "tech"),
    ("Will {country} {action} by {date}?", "world-affairs"),
    ("Will {movie} gross over ${box}M opening weekend?", "entertainment"),
    ("Will {candidate} win the {state} primary?", "elections"),
    ("Will {experiment} results confirm {hypothesis}?", "science"),
]

ENTITIES = ["Trump", "Harris", "DeSantis", "Newsom", "Biden", "Haley", "Vance"]
CRYPTOS = ["Bitcoin", "Ethereum", "Solana", "XRP", "Dogecoin"]
TEAMS = ["Lakers", "Chiefs", "Yankees", "Real Madrid", "Warriors"]
COMPANIES = ["Tesla", "Apple", "Nvidia", "Meta", "Google", "Amazon"]
COUNTRIES = ["China", "Russia", "Iran", "North Korea", "India"]


def generate_leaderboard(n: int = 100) -> pd.DataFrame:
    """Generate a realistic leaderboard of top traders."""
    rows = []
    for i in range(n):
        # PnL follows a power law — a few whales, many moderate winners
        pnl = float(np.random.pareto(1.5) * 15000 + 500)
        volume = pnl * np.random.uniform(3, 15)

        rows.append({
            "rank": i + 1,
            "address": f"0x{random.randbytes(20).hex()}",
            "username": f"trader_{i+1:03d}" if random.random() > 0.3 else f"whale_{i+1}",
            "pnl": round(pnl, 2),
            "volume": round(volume, 2),
        })

    df = pd.DataFrame(rows)
    df = df.sort_values("pnl", ascending=False).reset_index(drop=True)
    df["rank"] = range(1, len(df) + 1)
    return df


def generate_wallet_trades(address: str, n_trades: int = None,
                            skill_level: float = 0.6) -> pd.DataFrame:
    """
    Generate realistic trade history for a wallet.
    skill_level: 0.0 (random) to 1.0 (very skilled)
    """
    if n_trades is None:
        n_trades = random.randint(50, 400)

    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=random.randint(150, 365))

    rows = []
    for i in range(n_trades):
        # Timestamp: spread across history
        ts = start_date + timedelta(
            seconds=int((now - start_date).total_seconds() * (i / n_trades))
        )
        ts += timedelta(minutes=random.randint(-120, 120))

        # Side: skilled traders favor BUY on value bets
        side = "BUY" if random.random() < 0.72 else "SELL"

        # Price: skilled traders tend to enter at value prices (0.25-0.55)
        if random.random() < skill_level:
            price = np.clip(np.random.normal(0.38, 0.10), 0.05, 0.95)
        else:
            price = np.random.uniform(0.10, 0.90)

        # Size: log-normal distribution
        size = float(np.random.lognormal(3.5, 1.2))
        size = min(size, 5000)

        # Market
        template, category = random.choice(MARKET_TEMPLATES)
        condition_id = f"0x{random.randbytes(16).hex()}"
        market_title = _fill_template(template)

        rows.append({
            "timestamp": ts,
            "condition_id": condition_id,
            "market_title": market_title,
            "side": side,
            "price": round(price, 4),
            "size": round(size, 2),
            "outcome": random.choice(["Yes", "No"]),
            "outcome_index": random.randint(0, 1),
            "slug": category + "-" + market_title.lower().replace(" ", "-")[:40],
            "tx_hash": f"0x{random.randbytes(32).hex()}",
        })

    df = pd.DataFrame(rows)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def generate_active_markets(n: int = 200) -> pd.DataFrame:
    """Generate active markets with realistic data."""
    now = datetime.now(timezone.utc)
    rows = []

    for i in range(n):
        template, category = random.choice(MARKET_TEMPLATES)
        title = _fill_template(template)

        # Prices: Yes + No should sum close to 1.0
        yes_price = round(np.random.uniform(0.05, 0.95), 4)
        no_price = round(1.0 - yes_price, 4)

        volume = float(np.random.pareto(1.2) * 5000 + 100)
        liquidity = volume * np.random.uniform(0.02, 0.15)

        end_date = now + timedelta(days=random.randint(1, 180))
        condition_id = f"0x{random.randbytes(16).hex()}"
        token_yes = f"tok_{random.randbytes(8).hex()}"
        token_no = f"tok_{random.randbytes(8).hex()}"

        rows.append({
            "condition_id": condition_id,
            "title": title,
            "slug": title.lower().replace(" ", "-")[:40],
            "volume": round(volume, 2),
            "liquidity": round(liquidity, 2),
            "outcome_prices": [yes_price, no_price],
            "clob_token_ids": [token_yes, token_no],
            "end_date": end_date,
            "category": category,
            "active": True,
            "outcomes": "Yes,No",
        })

    return pd.DataFrame(rows)


def _fill_template(template: str) -> str:
    """Fill a market question template with random values."""
    replacements = {
        "{entity}": random.choice(ENTITIES),
        "{event}": random.choice(["2026 election", "nomination", "debate", "poll"]),
        "{crypto}": random.choice(CRYPTOS),
        "{price}": str(random.choice([50, 100, 150, 200, 250])),
        "{date}": random.choice(["Q2 2026", "June 2026", "end of 2026", "July 2026"]),
        "{team}": random.choice(TEAMS),
        "{league}": random.choice(["NBA", "NFL", "MLB", "Champions League"]),
        "{action}": random.choice(["cut", "raise", "hold", "pause"]),
        "{month}": random.choice(["April", "May", "June", "July"]),
        "{company}": random.choice(COMPANIES),
        "{sprice}": str(random.choice([150, 200, 250, 300, 500, 1000])),
        "{country}": random.choice(COUNTRIES),
        "{thing}": random.choice(["a new product", "layoffs", "acquisition", "IPO"]),
        "{movie}": random.choice(["Avatar 4", "Avengers 7", "Dune 3", "The Batman 2"]),
        "{box}": str(random.choice([100, 200, 300, 500])),
        "{candidate}": random.choice(ENTITIES),
        "{state}": random.choice(["Iowa", "New Hampshire", "South Carolina", "Nevada"]),
        "{experiment}": random.choice(["CERN", "Webb Telescope", "LIGO", "Fermilab"]),
        "{hypothesis}": random.choice(["dark matter", "new particles", "exoplanet life"]),
    }
    result = template
    for k, v in replacements.items():
        result = result.replace(k, v, 1)
    return result
