"""Dry run — execute a single trading cycle for debugging.

Usage:
  python dry_run.py              # regular cycle
  python dry_run.py --premarket  # pre-market cycle

Or run via VS Code debugger:
  - Select "Dry Run — Single Trading Cycle" configuration
  - Press F5 or Run > Start Debugging
"""

import sys
import argparse
from scheduler.loop import run_trading_cycle
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Execute a single trading cycle for debugging")
    parser.add_argument("--premarket", action="store_true", help="Run as pre-market cycle")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"Dry Run — {'Pre-Market' if args.premarket else 'Regular'} Trading Cycle")
    print(f"{'='*60}\n")

    result = run_trading_cycle(is_premarket=args.premarket)

    print(f"\n{'='*60}")
    print("Dry Run Complete")
    print(f"{'='*60}")
    print(f"Success: {result['success']}")
    print(f"Cycle ID: {result['cycle_id']}")
    print(f"Tickers Discovered: {result['tickers_discovered']}")
    print(f"Signals Generated: {result['signals_generated']}")
    print(f"Trades Executed: {result['trades_executed']}")
    print(f"Execution Time: {result['execution_time_ms']}ms")
    if result.get('errors'):
        print(f"Errors ({len(result['errors'])}):")
        for err in result['errors']:
            print(f"  - {err}")
