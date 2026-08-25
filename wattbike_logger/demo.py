"""Generatore di dati finti per provare l'export Excel senza chiavetta."""

from __future__ import annotations

import math
import time
from typing import Any

from .recorder import power_row


class _FakePower:
    def __init__(self) -> None:
        self.instantaneous_power = 0
        self.average_power = 0
        self.left_power = -1
        self.right_power = -1
        self.torque = 0.0
        self.angular_velocity = 0.0
        self.cadence = 255


def generate_demo_rows(seconds: float = 30.0, hz: float = 4.0, device_id: int = 9999) -> list[dict[str, Any]]:
    """Simula ~4 Hz di pagine standard_power come fa una Wattbike."""
    started = time.time()
    rows: list[dict[str, Any]] = []
    n = max(1, int(seconds * hz))
    event = 0
    accum = 0

    for i in range(n):
        t = i / hz
        # Profilo semplice: riscaldamento + intervalli.
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
                0x10,  # standard power page
                event,
                0xFF,  # pedal power not used
                cadence & 0xFF,
                accum & 0xFF,
                (accum >> 8) & 0xFF,
                power & 0xFF,
                (power >> 8) & 0xFF,
            ]
        )
        fake = _FakePower()
        fake.instantaneous_power = power
        fake.average_power = power
        fake.cadence = cadence
        fake.torque = round(power / max(cadence * math.pi / 30, 1e-6), 3)
        fake.angular_velocity = round(cadence * math.pi / 30, 4)

        # timestamp coerente con elapsed simulato
        row = power_row(
            device_id=device_id,
            page=0x10,
            page_name="standard_power",
            power_data=fake,
            raw=raw,
            started_at=started,
        )
        row["elapsed_s"] = round(t, 3)
        rows.append(row)

    return rows
