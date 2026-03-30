#!/usr/bin/env python3
"""
run_live.py — 24-hour live simulation loop.

Runs the research + simulation pipeline every INTERVAL_HOURS, accumulating
positions and tracking performance across runs. Opens the dashboard in the
browser after each cycle so you can check progress at any time.

Usage:
  python run_live.py                    # Run every 2h for 24h
  python run_live.py --interval 4       # Run every 4 hours
  python run_live.py --duration 48      # Run for 48 hours
  python run_live.py --once             # Single run (same as main.py --quick --simulate)
  python run_live.py --demo             # Use synthetic data (offline test)
"""

import argparse
import os
import sys
import time
import subprocess
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from dashboard import generate_dashboard

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "live_run.log")


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run_cycle(demo: bool = False, quick: bool = True) -> bool:
    """Run one research + simulation cycle. Returns True on success."""
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, "main.py"),
           "--simulate", "--skip-dashboard"]

    if quick:
        cmd.append("--quick")
    if demo:
        cmd.append("--demo")

    log(f"Starting cycle: {' '.join(os.path.basename(c) for c in cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=False,
            text=True,
            cwd=SCRIPT_DIR,
        )
        success = result.returncode == 0
        log(f"Cycle finished — exit code {result.returncode}")
        return success
    except Exception as e:
        log(f"Cycle error: {e}")
        return False


def print_status(cycle: int, total: int, next_run: datetime, start: datetime):
    elapsed = datetime.now(timezone.utc) - start
    hours_left = (next_run - datetime.now(timezone.utc)).total_seconds() / 3600
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  LIVE RUN STATUS
║  Cycle {cycle}/{total} complete
║  Elapsed:    {str(elapsed).split('.')[0]}
║  Next run:   {next_run.strftime('%H:%M UTC')}  ({hours_left:.1f}h from now)
║  Dashboard:  Open dashboard.html in your browser anytime
╚══════════════════════════════════════════════════════════════╝""")


def main():
    parser = argparse.ArgumentParser(description="24-hour Polymarket live simulation loop")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="Hours between cycles (default: 2)")
    parser.add_argument("--duration", type=float, default=24.0,
                        help="Total hours to run (default: 24)")
    parser.add_argument("--once", action="store_true",
                        help="Run a single cycle and exit")
    parser.add_argument("--demo", action="store_true",
                        help="Use synthetic demo data (no real API)")
    args = parser.parse_args()

    interval_secs = args.interval * 3600
    total_cycles = 1 if args.once else max(1, int(args.duration / args.interval))
    start_time = datetime.now(timezone.utc)
    end_time = start_time + timedelta(hours=args.duration)

    mode = "DEMO" if args.demo else "LIVE"
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║        POLYMARKET 24-HOUR LIVE SIMULATION RUNNER            ║
╠══════════════════════════════════════════════════════════════╣
║  Mode:       {mode:<48}║
║  Interval:   Every {args.interval:.0f}h{" " * 44}║
║  Duration:   {args.duration:.0f} hours ({total_cycles} cycles){" " * 35}║
║  Started:    {start_time.strftime('%Y-%m-%d %H:%M UTC'):<48}║
║  Ends:       {end_time.strftime('%Y-%m-%d %H:%M UTC'):<48}║
║  Database:   {os.path.basename(config.DB_PATH):<48}║
║  Log:        live_run.log{" " * 37}║
╠══════════════════════════════════════════════════════════════╣
║  Fees: {config.TAKER_FEE_PCT:.0%} entry + {config.WINNER_FEE_PCT:.0%} on profits (Polymarket CLOB rates) {" " * 22}║
║  Press Ctrl+C at any time to stop and view final dashboard  ║
╚══════════════════════════════════════════════════════════════╝
""")

    log(f"=== Live run started: {total_cycles} cycles, {args.interval}h interval ===")

    completed = 0
    try:
        for cycle in range(1, total_cycles + 1):
            print(f"\n{'─'*60}")
            print(f"  CYCLE {cycle}/{total_cycles}  —  {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
            print(f"{'─'*60}")

            success = run_cycle(demo=args.demo)

            # Regenerate dashboard after each cycle
            try:
                dash_path = os.path.join(SCRIPT_DIR, "dashboard.html")
                generate_dashboard(
                    db_path=config.DB_PATH,
                    output_path=dash_path,
                    open_browser=(cycle == 1),   # Only auto-open on first cycle
                )
                log(f"Dashboard updated: {dash_path}")
            except Exception as e:
                log(f"Dashboard error: {e}")

            completed += 1

            if cycle < total_cycles:
                next_run = datetime.now(timezone.utc) + timedelta(seconds=interval_secs)
                print_status(cycle, total_cycles, next_run, start_time)
                log(f"Sleeping {args.interval}h until {next_run.strftime('%H:%M UTC')}")

                # Sleep in 60s chunks so Ctrl+C is responsive
                sleep_remaining = interval_secs
                while sleep_remaining > 0:
                    chunk = min(60, sleep_remaining)
                    time.sleep(chunk)
                    sleep_remaining -= chunk

    except KeyboardInterrupt:
        print(f"\n\n  ⚠  Interrupted after {completed} cycles.")
        log(f"Run interrupted after {completed} cycles.")

    # Final dashboard
    print(f"\n{'='*60}")
    print(f"  RUN COMPLETE — {completed} cycles in {str(datetime.now(timezone.utc) - start_time).split('.')[0]}")
    print(f"{'='*60}")

    log("=== Generating final dashboard ===")
    try:
        dash_path = os.path.join(SCRIPT_DIR, "dashboard.html")
        generate_dashboard(
            db_path=config.DB_PATH,
            output_path=dash_path,
            open_browser=True,
        )
    except Exception as e:
        log(f"Final dashboard error: {e}")

    log(f"=== Live run ended: {completed} cycles completed ===")
    print(f"\n  Log saved to: {LOG_FILE}")


if __name__ == "__main__":
    main()
