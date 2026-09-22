"""Run every parser test suite. Exit non-zero if any test fails."""
import subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
suites = sorted(p for p in ROOT.glob("test_*.py"))
failed = []
for suite in suites:
    print(f"\n=== {suite.name} ===")
    result = subprocess.run([sys.executable, str(suite)])
    if result.returncode != 0:
        failed.append(suite.name)
print("\n" + "=" * 50)
print("ALL SUITES PASSED" if not failed else f"FAILED: {', '.join(failed)}")
sys.exit(1 if failed else 0)
