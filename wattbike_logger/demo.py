"""Generatore di dati finti per provare l'export Excel senza chiavetta."""

from __future__ import annotations

import math
import time
from typing import Any

from .recorder import make_row


def generate_demo_rows(seconds: float = 30.0, hz: float = 4.0, device_id: int = 9999) -> list[dict[str, Any]]:
    """Simula ~4 Hz di pagine standard_power come una Wattbike ideale."""
    started = time.time()
    rows: list[dict[str, Any]] = []
    n = max(1, int(seconds * hz))
    event = 0
    accum = 0

    for i in range(n):
        t = i / hz
        cadence = int(70 + 25 * math.sin(t / 8) + 5 * math.sin(t / 2))
        cadence = max(40, min(120, cadence))
        base = 180 + 80 * math.sin(t / 10)
        if int(t) % 20 >= 10:
            base += 120
        power = max(0, int(base + 15 * math.sin(t * 2)))
        event = (event + 1) % 256
        accum = (accum + power) % 65536

        raw = bytes(
            [
                0x10,
                event,
                0xFF,
                cadence & 0xFF,
                accum & 0xFF,
                (accum >> 8) & 0xFF,
                power & 0xFF,
                (power >> 8) & 0xFF,
            ]
        )
        row = make_row(
            device_id=device_id,
            raw=raw,
            started_at=started,
            device_type=11,  # PowerMeter
        )
        row["elapsed_s"] = round(t, 3)
        rows.append(row)

    return rows
