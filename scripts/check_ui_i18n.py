"""Reject new browser copy unless it is placed in the i18n catalogue.

The legacy frontend still contains Chinese copy. Its *copy tokens* are locked
in ``i18n-legacy-baseline.json`` while ordinary JavaScript and HTML structure
and standalone developer comments remain editable. This keeps the migration
gate useful instead of making every security fix or explanation require a
meaningless whole-file checksum refresh.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "webapp" / "static"
CATALOGUE = STATIC_DIR / "i18n" / "zh-CN.json"
BASELINE = STATIC_DIR / "i18n-legacy-baseline.json"
TEXT_PATTERN = re.compile(r"[\u4e00-\u9fff]")
COPY_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]+")


def code_lines(text: str) -> list[tuple[int, str]]:
    """Return source lines excluding standalone JS/HTML comment blocks."""
    result: list[tuple[int, str]] = []
    block_end = ""
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if block_end:
            if block_end in stripped:
                block_end = ""
            continue
        if stripped.startswith("//"):
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped[2:]:
                block_end = "*/"
            continue
        if stripped.startswith("<!--"):
            if "-->" not in stripped[4:]:
                block_end = "-->"
            continue
        result.append((number, line))
    return result


def copy_signature(path: Path) -> dict[str, object]:
    """Return a stable signature of UI copy, ignoring code and comments."""
    text = path.read_text(encoding="utf-8-sig")
    tokens = [token for _number, line in code_lines(text) for token in COPY_TOKEN_PATTERN.findall(line)]
    normalized = "\n".join(tokens).encode("utf-8")
    return {"copy_sha256": hashlib.sha256(normalized).hexdigest(), "copy_tokens": len(tokens)}


def main() -> int:
    if not CATALOGUE.exists():
        print(f"{CATALOGUE.relative_to(ROOT)}: ERROR i18n: missing zh-CN catalogue. Fix: add all UI text to the catalogue.")
        return 1
    json.loads(CATALOGUE.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    violations: list[tuple[Path, int]] = []
    for path in STATIC_DIR.rglob("*"):
        if path.suffix not in {".js", ".html"}:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative in baseline:
            expected = baseline[relative]
            actual = copy_signature(path)
            if not isinstance(expected, dict) or actual != expected:
                print(f"{relative}: ERROR i18n: legacy UI copy changed. Fix: migrate touched copy to window.t(key), then update/remove its baseline entry.")
                return 1
            continue
        for number, line in code_lines(path.read_text(encoding="utf-8-sig")):
            if TEXT_PATTERN.search(line):
                violations.append((path, number))
    if violations:
        for path, number in violations:
            print(f"{path.relative_to(ROOT)}:{number}: ERROR i18n: hard-coded browser text. Fix: use window.t(key) with webapp/static/i18n/zh-CN.json.")
        return 1
    print("i18n check passed: new browser UI has no hard-coded Chinese copy; legacy copy tokens are locked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
