"""Run the whole test suite with one command.

    python tests/run_all.py

Discovers every `tests/test_*.py`, runs each via its own zero-dependency
runner (subprocess, so one file's import error can't abort the rest), and
prints an aggregate summary. Exits non-zero if any file fails or errors — so
it's usable as a CI step and as the auto-test Stop hook.

`pytest tests/` still works too; this is the no-dependency path.
"""
import subprocess
import sys
import time
from pathlib import Path

_TESTS_DIR = Path(__file__).parent


def main() -> int:
    files = sorted(p for p in _TESTS_DIR.glob("test_*.py"))
    if not files:
        print("no test_*.py files found")
        return 0

    failed = []
    start = time.monotonic()
    for f in files:
        print(f"\n=== {f.name} " + "=" * max(0, 48 - len(f.name)))
        result = subprocess.run([sys.executable, str(f)])
        if result.returncode != 0:
            failed.append(f.name)

    elapsed = time.monotonic() - start
    print("\n" + "=" * 56)
    passed = len(files) - len(failed)
    print(f"SUITE: {passed}/{len(files)} files passed in {elapsed:.1f}s")
    if failed:
        print("FAILED FILES: " + ", ".join(failed))
        return 1
    print("ALL TEST FILES PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
