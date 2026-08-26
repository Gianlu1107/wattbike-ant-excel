"""Entry point per PyInstaller / avvio diretto (GUI senza console)."""

from __future__ import annotations

import os
import sys


def _silence_console_when_frozen() -> None:
    """Evita output residuo se l'exe windowed viene lanciato da Terminale."""
    if not getattr(sys, "frozen", False):
        return
    try:
        devnull = open(os.devnull, "w", encoding="utf-8")
        sys.stdout = devnull  # type: ignore[assignment]
        sys.stderr = devnull  # type: ignore[assignment]
    except Exception:
        pass


_silence_console_when_frozen()

from wattbike_logger.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
