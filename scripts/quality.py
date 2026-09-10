"""Run all quality gates and report each failed tool with a repair command."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_SCOPES = ["idor_workbench", "webapp", "tests", "scripts"]
COMMANDS = [
    ("architecture", [sys.executable, "scripts/check_architecture.py"]),
    ("i18n", [sys.executable, "scripts/check_ui_i18n.py"]),
    ("syntax", [sys.executable, "-m", "compileall", "-q", *PYTHON_SCOPES]),
    ("tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]),
    ("ruff", [sys.executable, "-m", "ruff", "check", *PYTHON_SCOPES]),
    ("mypy", [sys.executable, "-m", "mypy"]),
    ("dead-code", [sys.executable, "-m", "vulture", "idor_workbench", "webapp", "tests", "--min-confidence", "80"]),
]


def main() -> int:
    failed = False
    required_modules = {"ruff": "ruff", "mypy": "mypy", "dead-code": "vulture"}
    for name, command in COMMANDS:
        if name in required_modules and importlib.util.find_spec(required_modules[name]) is None:
            print(f"{name}: ERROR quality tool unavailable. Fix: pip install -r requirements-dev.txt")
            failed = True
            continue
        print(f"\n== {name} ==")
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            print(f"{name}: FAILED. Fix the diagnostics above; required tools install with: pip install -r requirements-dev.txt")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
