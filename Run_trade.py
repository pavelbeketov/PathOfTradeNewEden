"""Run all three trading stages in order and show immediate, useful progress."""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STAGES = [
    ("1/3", "Downloading and validating market orders", "Orders_in_a_region.py"),
    ("2/3", "Calculating the route and deal tables", "Calculate_profit.py"),
    ("3/3", "Building the HTML report", "Make_report.py"),
]


def main():
    started = time.perf_counter()
    for number, title, script in STAGES:
        print(f"\n[{number}] {title}...", flush=True)
        result = subprocess.run([sys.executable, "-u", str(ROOT / script)], cwd=ROOT, check=False)
        if result.returncode:
            print(f"\nFAILED: {script} exited with code {result.returncode}.", flush=True)
            print("The next stage was not started. Correct the error and run again.", flush=True)
            return result.returncode
        print(f"[{number}] Complete.", flush=True)
    print(f"\nAll stages completed in {(time.perf_counter()-started)/60:.1f} minutes.", flush=True)
    print(f"Report: {ROOT / 'results' / 'arbitrage_report.html'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
