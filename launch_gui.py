"""Entry point per PyInstaller / avvio diretto (GUI senza console)."""

from __future__ import annotations

import os
import sys


def _silence_console_when_frozen() -> None:
    """Evita output residuo se l'exe windowed viene lanciato da Terminale (solo GUI)."""
    if not getattr(sys, "frozen", False):
        return
    cli = any(
        a in ("setup-drivers", "diagnose", "scan", "record", "demo", "--help", "-h")
        for a in sys.argv[1:]
    )
    if cli:
        # Exe windowed: alloca una console così print/redirect funzionano
        if sys.platform.startswith("win"):
            try:
                import ctypes

                ctypes.windll.kernel32.AllocConsole()
                sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
                sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
                sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
            except Exception:
                pass
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
