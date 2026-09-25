"""
scripts/export_translation_review.py

Writes one CSV per language for a native speaker to review, so checking the
drafted translations is a 15-minute job instead of a hunt through source files.

    python scripts/export_translation_review.py

Reads frontend/src/i18n.tsx (UI strings) and shared/alert_texts.py (HOLD /
REJECT / RECALL alerts) and writes docs/translation_review/<lang>.csv with
columns: kind, key, english, draft, reviewer_ok, correction.

The reviewer fills the last two columns. Safety alerts are listed first because
a wrong word there matters most. Nothing here reviews or improves a translation;
it only puts the text in front of the person who can.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.alert_texts import ALERT_TEXTS  # noqa: E402

I18N = ROOT / "frontend" / "src" / "i18n.tsx"
OUT = ROOT / "docs" / "translation_review"

_ENTRY = re.compile(r"^\s+([A-Za-z0-9_]+):\s*'((?:[^'\\]|\\.)*)',?\s*$")


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n"}.get(m.group(1), m.group(1)), value)


def _parse_i18n(text: str) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    lines = text.replace("\r\n", "\n").split("\n")
    english: dict[str, str] = {}
    langs: dict[str, dict[str, str]] = {}
    section: str | None = None
    for line in lines:
        if line.startswith("const EN = {"):
            section = "en"
            continue
        if section == "en" and line.startswith("} as const"):
            section = None
            continue
        if line.startswith("const STRINGS"):
            section = "strings"
            continue
        if section == "strings":
            m = re.match(r"^  ([a-z]{2,3}): \{\s*$", line)
            if m:
                current = m.group(1)
                langs[current] = {}
                continue
            if line.startswith("  },"):
                current = None
                continue
            if line.startswith("};"):
                section = None
                continue
        entry = _ENTRY.match(line)
        if not entry:
            continue
        key, value = entry.group(1), _unescape(entry.group(2))
        if section == "en":
            english[key] = value
        elif section == "strings" and langs and current:
            langs[current][key] = value
    return english, langs


def main() -> None:
    english, ui = _parse_i18n(I18N.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)

    languages = sorted((set(ui) | set(ALERT_TEXTS)) - {"en"})
    for lang in languages:
        rows = []
        for status, draft in ALERT_TEXTS.get(lang, {}).items():
            rows.append(("alert", status, ALERT_TEXTS["en"].get(status, ""), draft))
        for key, draft in ui.get(lang, {}).items():
            rows.append(("ui", key, english.get(key, ""), draft))
        with (OUT / f"{lang}.csv").open("w", encoding="utf-8-sig", newline="") as fh:  # BOM so Excel reads Indic scripts
            writer = csv.writer(fh)
            writer.writerow(["kind", "key", "english", "draft", "reviewer_ok", "correction"])
            for kind, key, en, draft in rows:
                writer.writerow([kind, key, en, draft, "", ""])
        print(f"{lang}: {len(rows)} rows")


if __name__ == "__main__":
    main()
