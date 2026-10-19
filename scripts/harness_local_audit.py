#!/usr/bin/env python3
"""Package-local MaxKB chat-contract audit (no workspace root required)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    errors = 0
    claude = ROOT / "CLAUDE.md"
    if not claude.is_file() or "python main.py" not in claude.read_text(encoding="utf-8"):
        print("[ERROR] CLAUDE.md missing or lacks python main.py commands")
        errors += 1

    urls = ROOT / "apps" / "chat" / "urls.py"
    if not urls.is_file():
        print("[ERROR] apps/chat/urls.py missing")
        errors += 1
    else:
        text = urls.read_text(encoding="utf-8", errors="ignore")
        for needle in ("chat/completions", "application_id"):
            if needle not in text:
                print(f"[ERROR] urls.py missing {needle!r}")
                errors += 1

    ser = ROOT / "apps" / "chat" / "serializers" / "chat.py"
    if not ser.is_file():
        print("[ERROR] apps/chat/serializers/chat.py missing")
        errors += 1
    else:
        text = ser.read_text(encoding="utf-8", errors="ignore")
        for needle in ("form_data", "chat_id"):
            if needle not in text:
                print(f"[ERROR] chat serializer missing {needle!r}")
                errors += 1

    if errors == 0:
        print("[OK] MaxKB local harness audit passed")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
