"""
Dashboard Generator — Creates a self-contained HTML report from the SQLite database.
Opens automatically in the user's default browser after generation.
"""

import os
import json
import sqlite3
import webbrowser
from datetime import datetime, timezone

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config


def _query(db_path, sql, params=()):
    """Run a SQL query and return rows as list of dicts."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _result_badge(t):
    """Return HTML badge for a sim trade result (avoids backslash-in-f-string issue)."""
    pnl = t.get("pnl") or 0
    resolved = t.get("resolved")
    if not resolved:
        return "⏳"
    if pnl > 0:
        return '<span class="badge badge-pass">WIN</span>'
    return '<span class="badge badge-fail">LOSS</span>'


def generate_dashboard(db_path=None, output_path=None, open_browser=True):
    """Generate an HTML dashboard from the bot's database."""
    db_path = db_path or config.DB_PATH
    if not os.path.exists(db_path):
        print(f"  ✗ Database not found: {db_path}")
        return None

    # ─── Pull data ─────────────────────────────────────────────────
    wallets = _query(db_path, "SELECT * FROM wallets ORDER BY score DESC")
    trades = _query(db_path, "SELECT * FROM trades ORDER BY timestamp DESC LIMIT 5000")
    markets = _query(db_path, "SELECT * FROM markets ORDER BY volume DESC LIMIT 200")
    sim_trades = _query(db_path, "SELECT * FROM simulated_trades ORDER BY timestamp ASC")
    patterns = _query(db_path, "SELECT * FROM patterns")

    # Forward simulation metadata
    bot_state = {row["key"]: row["value"] for row in _query(db_path,
        "SELECT key, value FROM bot_state") if "key" in row}
    forward_start_date = bot_state.get("forward_start_date", "")
    if forward_start_date:
        try:
            fsd = datetime.fromisoformat(forward_start_date.replace("Z", "+00:00"))
            days_running = max(0, (datetime.now(timezone.utc) - fsd).days)
            forward_start_label = fsd.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            days_running = 0
            forward_start_label = forward_start_date[:16]
    else:
        days_running = 0
        forward_start_label = "Not started yet"

    passing_wallets = [w for w in wallets if w.get("passes_filters")]
    failing_wallets = [w for w in wallets if not w.get("passes_filters")]

    # ─── Compute sim performance ───────────────────────────────────
    resolved_trades = [t for t in sim_trades if t.get("resolved")]
    wins = [t for t in resolved_trades if (t.get("pnl") or 0) > 0]
    losses = [t for t in resolved_trades if (t.get("pnl") or 0) <= 0]
    total_pnl = sum(t.get("pnl", 0) or 0 for t in resolved_trades)
    total_fees = sum(t.get("fees_paid", 0) or 0 for t in sim_trades)
    win_rate = len(wins) / len(resolved_trades) * 100 if resolved_trades else 0

    # Equity curve (forward-only: starts at INITIAL_BANKROLL, never replays history)
    equity_curve = []
    running = config.INITIAL_BANKROLL
    for t in sim_trades:
        if t.get("bankroll_after") is not None:
            running = float(t["bankroll_after"])
        equity_curve.append({"x": len(equity_curve), "y": round(running, 2)})

    # Max drawdown
    peak = config.INITIAL_BANKROLL
    max_dd = 0
    for pt in equity_curve:
        if pt["y"] > peak:
            peak = pt["y"]
        dd = (peak - pt["y"]) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    # Trade PnL distribution
    pnl_values = [round(t.get("pnl", 0) or 0, 2) for t in resolved_trades]

    # Category breakdown from trades
    category_counts = {}
    for t in trades:
        cat = t.get("category", "unknown") or "unknown"
        category_counts[cat] = category_counts.get(cat, 0) + 1

    # Price distribution of trades
    trade_prices = [t.get("price", 0) for t in trades if t.get("price")]

    # Top markets by volume
    top_markets_data = []
    for m in markets[:20]:
        top_markets_data.append({
            "title": (m.get("title") or "Unknown")[:50],
            "volume": m.get("volume", 0),
            "price": m.get("best_bid", m.get("last_price", 0)) or 0,
        })

    # Pattern data
    pattern_data = {}
    for p in patterns:
        try:
            pattern_data[p["pattern_type"]] = json.loads(p.get("pattern_data", "{}"))
        except (json.JSONDecodeError, TypeError):
            pass

    # ─── Build HTML ────────────────────────────────────────────────
    final_bankroll = equity_curve[-1]["y"] if equity_curve else config.INITIAL_BANKROLL
    total_return_pct = ((final_bankroll - config.INITIAL_BANKROLL) / config.INITIAL_BANKROLL) * 100

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Polymarket Research Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0a0e17;
    color: #e2e8f0;
    min-height: 100vh;
  }}
  .header {{
    background: linear-gradient(135deg, #1a1f35 0%, #0d1321 100%);
    border-bottom: 1px solid #1e293b;
    padding: 24px 32px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .header h1 {{
    font-size: 22px;
    font-weight: 700;
    background: linear-gradient(135deg, #60a5fa, #a78bfa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .header .meta {{ color: #64748b; font-size: 13px; }}
  .container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}

  /* KPI Cards */
  .kpi-row {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }}
  .kpi {{
    background: #111827;
    border: 1px solid #1e293b;
    border-radius: 12px;
    padding: 20px;
    text-align: center;
  }}
  .kpi .label {{ font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; }}
  .kpi .value {{ font-size: 28px; font-weight: 700; }}
  .kpi .sub {{ font-size: 12px; color: #64748b; margin-top: 4px; }}
  .green {{ color: #34d399; }}
  .red {{ color: #f87171; }}
  .blue {{ color: #60a5fa; }}
  .purple {{ color: #a78bfa; }}
  .yellow {{ color: #fbbf24; }}

  /* Sections */
  .section {{
    background: #111827;
    border: 1px solid #1e293b;
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 24px;
  }}
  .section h2 {{
    font-size: 16px;
    font-weight: 600;
    margin-bottom: 16px;
    color: #94a3b8;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .section h2 .icon {{ font-size: 20px; }}

  /* Charts grid */
  .charts-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 24px;
    margin-bottom: 24px;
  }}
  @media (max-width: 900px) {{ .charts-grid {{ grid-template-columns: 1fr; }} }}
  .chart-box {{
    background: #111827;
    border: 1px solid #1e293b;
    border-radius: 12px;
    padding: 20px;
  }}
  .chart-box h3 {{ font-size: 14px; color: #94a3b8; margin-bottom: 12px; }}
  .chart-container {{ position: relative; height: 280px; }}

  /* Tables */
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }}
  th {{
    text-align: left;
    padding: 10px 12px;
    background: #0f172a;
    color: #64748b;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    font-size: 11px;
    border-bottom: 1px solid #1e293b;
  }}
  td {{
    padding: 10px 12px;
    border-bottom: 1px solid #1e293b;
  }}
  tr:hover {{ background: #0f172a; }}
  .badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 9999px;
    font-size: 11px;
    font-weight: 600;
  }}
  .badge-pass {{ background: #064e3b; color: #34d399; }}
  .badge-fail {{ background: #450a0a; color: #f87171; }}
  .badge-buy {{ background: #1e3a5f; color: #60a5fa; }}
  .badge-sell {{ background: #5b21b6; color: #c4b5fd; }}

  /* Market cards */
  .market-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }}
  .market-card {{
    background: #0f172a;
    border: 1px solid #1e293b;
    border-radius: 8px;
    padding: 14px;
  }}
  .market-card .title {{ font-size: 13px; font-weight: 600; margin-bottom: 6px; line-height: 1.4; }}
  .market-card .stats {{ display: flex; gap: 16px; font-size: 12px; color: #64748b; }}
  .market-card .stats span {{ display: flex; align-items: center; gap: 4px; }}

  /* Scrollable table */
  .table-wrap {{ max-height: 500px; overflow-y: auto; }}
  .table-wrap::-webkit-scrollbar {{ width: 6px; }}
  .table-wrap::-webkit-scrollbar-track {{ background: #111827; }}
  .table-wrap::-webkit-scrollbar-thumb {{ background: #334155; border-radius: 3px; }}

  .tab-bar {{ display: flex; gap: 4px; margin-bottom: 16px; }}
  .tab {{
    padding: 8px 16px;
    border-radius: 8px;
    font-size: 13px;
    cursor: pointer;
    background: #0f172a;
    border: 1px solid #1e293b;
    color: #94a3b8;
    transition: all 0.2s;
  }}
  .tab.active {{ background: #1e293b; color: #e2e8f0; border-color: #334155; }}
  .tab:hover {{ background: #1e293b; }}
  .tab-content {{ display: none; }}
  .tab-content.active {{ display: block; }}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>Polymarket Research Dashboard</h1>
    <div class="meta">Generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")} &middot; Database: {os.path.basename(db_path)}</div>
  </div>
  <div class="meta">
    {len(wallets)} wallets &middot; {len(trades)} trades &middot; {len(markets)} markets &middot; {len(sim_trades)} sim trades
  </div>
</div>

<div class="container">

  <!-- Forward-only mode banner -->
  <div style="background:#0f1a2e;border:1px solid #1e3a5f;border-radius:8px;padding:12px 20px;margin-bottom:20px;display:flex;align-items:center;gap:12px;">
    <span style="font-size:18px">🏁</span>
    <div>
      <span style="color:#60a5fa;font-weight:600;font-size:13px">FORWARD-ONLY MODE</span>
      <span style="color:#475569;font-size:12px;margin-left:12px">No backtesting — only real forward trades counted</span>
      <span style="color:#475569;font-size:12px;margin-left:12px">Started: {forward_start_label}</span>
    </div>
  </div>

  <!-- KPI Row -->
  <div class="kpi-row">
    <div class="kpi">
      <div class="label">Starting Capital</div>
      <div class="value blue">${config.INITIAL_BANKROLL:,.0f}</div>
      <div class="sub">configurable via env</div>
    </div>
    <div class="kpi">
      <div class="label">Current Capital</div>
      <div class="value {"green" if total_return_pct >= 0 else "red"}">${final_bankroll:,.2f}</div>
      <div class="sub">{"+" if total_return_pct >= 0 else ""}{total_return_pct:.1f}% return</div>
    </div>
    <div class="kpi">
      <div class="label">Days Running</div>
      <div class="value purple">{days_running}</div>
      <div class="sub">forward trades: {len(sim_trades)}</div>
    </div>
    <div class="kpi">
      <div class="label">Win Rate</div>
      <div class="value {"green" if win_rate > 50 else "yellow"}">{win_rate:.1f}%</div>
      <div class="sub">{len(wins)}W / {len(losses)}L of {len(resolved_trades)} resolved</div>
    </div>
    <div class="kpi">
      <div class="label">Max Drawdown</div>
      <div class="value {"red" if max_dd > 0.2 else "yellow"}">{max_dd:.1%}</div>
    </div>
    <div class="kpi">
      <div class="label">Wallets Tracked</div>
      <div class="value purple">{len(passing_wallets)}</div>
      <div class="sub">of {len(wallets)} scored</div>
    </div>
    <div class="kpi">
      <div class="label">Total P&amp;L</div>
      <div class="value {"green" if total_pnl >= 0 else "red"}">${total_pnl:+,.2f}</div>
      <div class="sub">fees paid: ${total_fees:.2f}</div>
    </div>
  </div>

  <!-- Charts Row -->
  <div class="charts-grid">
    <div class="chart-box">
      <h3>Equity Curve</h3>
      <div class="chart-container"><canvas id="equityChart"></canvas></div>
    </div>
    <div class="chart-box">
      <h3>Trade P&amp;L Distribution</h3>
      <div class="chart-container"><canvas id="pnlChart"></canvas></div>
    </div>
    <div class="chart-box">
      <h3>Entry Price Distribution</h3>
      <div class="chart-container"><canvas id="priceChart"></canvas></div>
    </div>
    <div class="chart-box">
      <h3>Wallet Scores</h3>
      <div class="chart-container"><canvas id="scoreChart"></canvas></div>
    </div>
  </div>

  <!-- Wallet Table -->
  <div class="section">
    <h2><span class="icon">👛</span> Scored Wallets</h2>
    <div class="tab-bar">
      <div class="tab active" onclick="switchTab(event, 'passing')">Passing ({len(passing_wallets)})</div>
      <div class="tab" onclick="switchTab(event, 'failing')">Filtered Out ({len(failing_wallets)})</div>
    </div>
    <div id="tab-passing" class="tab-content active">
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>Rank</th><th>Username</th><th>Score</th><th>PnL</th>
            <th>Trades</th><th>Win Rate</th><th>Avg Entry</th><th>Max DD</th><th>Consistency</th>
          </tr></thead>
          <tbody>
            {"".join(f'''<tr>
              <td>{i+1}</td>
              <td><strong>{w.get("username","anon")}</strong></td>
              <td><strong>{w.get("score",0):.3f}</strong></td>
              <td class="{"green" if w.get("pnl",0) > 0 else "red"}">${w.get("pnl",0):,.0f}</td>
              <td>{w.get("total_trades",0)}</td>
              <td>{w.get("win_rate",0):.1%}</td>
              <td>{w.get("avg_entry_price",0):.3f}</td>
              <td>{w.get("max_drawdown",0):.1%}</td>
              <td>{w.get("consistency",0):.3f}</td>
            </tr>''' for i, w in enumerate(passing_wallets))}
          </tbody>
        </table>
      </div>
    </div>
    <div id="tab-failing" class="tab-content">
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>Username</th><th>Score</th><th>PnL</th><th>Trades</th><th>Filter Reasons</th>
          </tr></thead>
          <tbody>
            {"".join(f'''<tr>
              <td>{w.get("username","anon")}</td>
              <td>{w.get("score",0):.3f}</td>
              <td>${w.get("pnl",0):,.0f}</td>
              <td>{w.get("total_trades",0)}</td>
              <td style="color:#f87171;font-size:12px">{w.get("filter_reasons","")}</td>
            </tr>''' for w in failing_wallets)}
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Simulated Trades -->
  <div class="section">
    <h2><span class="icon">📊</span> Simulated Trades (last 100)</h2>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Time</th><th>Market</th><th>Side</th><th>Entry</th>
          <th>Size</th><th>P&amp;L</th><th>Bankroll</th><th>Result</th>
        </tr></thead>
        <tbody>
          {"".join(f'''<tr>
            <td style="color:#64748b;font-size:12px">{str(t.get("timestamp",""))[:16]}</td>
            <td>{str(t.get("market_title",""))[:45]}</td>
            <td><span class="badge {"badge-buy" if t.get("side")=="BUY" else "badge-sell"}">{t.get("side","")}</span></td>
            <td>{t.get("entry_price",0):.2f}</td>
            <td>${t.get("size",0):.2f}</td>
            <td class="{"green" if (t.get("pnl") or 0) > 0 else "red"}">{f"${t.get('pnl',0):+.2f}" if t.get("pnl") is not None else "—"}</td>
            <td>${t.get("bankroll_after",0):,.2f}</td>
            <td>{_result_badge(t)}</td>
          </tr>''' for t in sim_trades[-100:])}
        </tbody>
      </table>
    </div>
  </div>

  <!-- Top Markets -->
  <div class="section">
    <h2><span class="icon">🎯</span> Top Markets by Volume</h2>
    <div class="market-grid">
      {"".join(f'''<div class="market-card">
        <div class="title">{m.get("title","")[:60]}</div>
        <div class="stats">
          <span>💰 ${m.get("volume",0):,.0f}</span>
          <span>📈 {m.get("price",0):.2f}</span>
        </div>
      </div>''' for m in top_markets_data[:12])}
    </div>
  </div>

</div>

<script>
  // Tab switching
  function switchTab(e, id) {{
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    e.target.classList.add('active');
    document.getElementById('tab-' + id).classList.add('active');
  }}

  // Chart defaults
  Chart.defaults.color = '#94a3b8';
  Chart.defaults.borderColor = '#1e293b';
  const gridColor = '#1e293b';

  // 1. Equity Curve
  new Chart(document.getElementById('equityChart'), {{
    type: 'line',
    data: {{
      labels: {json.dumps([p["x"] for p in equity_curve])},
      datasets: [{{
        label: 'Bankroll ($)',
        data: {json.dumps([p["y"] for p in equity_curve])},
        borderColor: '#60a5fa',
        backgroundColor: 'rgba(96,165,250,0.1)',
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 2,
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ display: false }},
        y: {{ grid: {{ color: gridColor }}, ticks: {{ callback: v => '$'+v }} }}
      }}
    }}
  }});

  // 2. PnL Distribution
  (() => {{
    const pnlVals = {json.dumps(pnl_values)};
    if (pnlVals.length === 0) return;
    const min = Math.floor(Math.min(...pnlVals));
    const max = Math.ceil(Math.max(...pnlVals));
    const binSize = Math.max(1, Math.ceil((max - min) / 20));
    const bins = {{}};
    for (let v of pnlVals) {{
      const b = Math.floor(v / binSize) * binSize;
      bins[b] = (bins[b] || 0) + 1;
    }}
    const sortedKeys = Object.keys(bins).map(Number).sort((a,b) => a-b);
    new Chart(document.getElementById('pnlChart'), {{
      type: 'bar',
      data: {{
        labels: sortedKeys.map(k => '$' + k),
        datasets: [{{
          label: 'Trades',
          data: sortedKeys.map(k => bins[k]),
          backgroundColor: sortedKeys.map(k => k >= 0 ? '#34d399' : '#f87171'),
          borderRadius: 3,
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          x: {{ grid: {{ display: false }} }},
          y: {{ grid: {{ color: gridColor }} }}
        }}
      }}
    }});
  }})();

  // 3. Entry Price Distribution
  (() => {{
    const prices = {json.dumps([round(p, 2) for p in trade_prices[:2000]])};
    if (prices.length === 0) return;
    const bins = {{}};
    for (let p of prices) {{
      const b = (Math.floor(p * 10) / 10).toFixed(1);
      bins[b] = (bins[b] || 0) + 1;
    }}
    const labels = Object.keys(bins).sort();
    new Chart(document.getElementById('priceChart'), {{
      type: 'bar',
      data: {{
        labels: labels,
        datasets: [{{
          label: 'Trades',
          data: labels.map(l => bins[l]),
          backgroundColor: '#a78bfa',
          borderRadius: 3,
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          x: {{ grid: {{ display: false }}, title: {{ display: true, text: 'Entry Price' }} }},
          y: {{ grid: {{ color: gridColor }} }}
        }}
      }}
    }});
  }})();

  // 4. Wallet Scores
  (() => {{
    const wallets = {json.dumps([{"name": w.get("username","anon")[:12], "score": w.get("score",0)} for w in wallets[:20]])};
    new Chart(document.getElementById('scoreChart'), {{
      type: 'bar',
      data: {{
        labels: wallets.map(w => w.name),
        datasets: [{{
          label: 'Score',
          data: wallets.map(w => w.score),
          backgroundColor: wallets.map(w => w.score > 0.3 ? '#34d399' : w.score > 0.1 ? '#fbbf24' : '#f87171'),
          borderRadius: 3,
        }}]
      }},
      options: {{
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          x: {{ grid: {{ color: gridColor }}, max: 1 }},
          y: {{ grid: {{ display: false }} }}
        }}
      }}
    }});
  }})();
</script>

</body>
</html>"""

    # ─── Write output ──────────────────────────────────────────────
    if output_path is None:
        # Put it next to the database or in the script directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_path = os.path.join(script_dir, "dashboard.html")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n  📊 Dashboard generated: {output_path}")

    if open_browser:
        try:
            webbrowser.open(f"file://{os.path.abspath(output_path)}")
            print(f"  🌐 Opened in browser")
        except Exception:
            print(f"  ℹ  Open this file in your browser to view")

    return output_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate Polymarket dashboard")
    parser.add_argument("--db", type=str, default=None, help="Path to SQLite database")
    parser.add_argument("--output", "-o", type=str, default=None, help="Output HTML path")
    parser.add_argument("--no-open", action="store_true", help="Don't auto-open browser")
    args = parser.parse_args()

    generate_dashboard(
        db_path=args.db,
        output_path=args.output,
        open_browser=not args.no_open,
    )
