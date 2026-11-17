"""Small pytest entry point. Default to the critical, isolated smoke suite."""
import argparse
from pathlib import Path
import subprocess
import sys


SUITES = {
    "smoke": ["-m", "smoke"],
    "unit": ["-m", "unit"],
    "integration": ["-m", "integration"],
    "all": [],
    "video": ["-m", "video", "--run-video"],
    "mysql": ["-m", "electronic_seal_mysql"],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=SUITES, nargs="?", default="smoke")
    args, extra = parser.parse_known_args()
    root = Path(__file__).resolve().parent
    report = root / "test-results" / f"{args.suite}.xml"
    report.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "pytest", "tests", "-q", "--tb=short",
        f"--junitxml={report}", *SUITES[args.suite], *extra,
    ]
    return subprocess.call(command, cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())
