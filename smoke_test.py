#!/usr/bin/env python3
"""Smoke test senza hardware ANT+."""

from __future__ import annotations

import tempfile
from pathlib import Path

from wattbike_logger import __version__
from wattbike_logger.demo import generate_demo_rows
from wattbike_logger.excel_export import COLUMNS, write_csv, write_xlsx
from wattbike_logger.ble_wattbike import enrich_kinematics, parse_cycling_power_measurement
from wattbike_logger.recorder import _decode_payload, timing_stats
from wattbike_logger.updater import is_newer


def main() -> None:
    assert __version__
    rows = generate_demo_rows(seconds=2, hz=4)
    assert len(rows) == 8
    assert all(c in rows[0] for c in COLUMNS)
    assert rows[0]["page"] == 0x10
    assert rows[0]["event_count"] == 1
    assert rows[0]["device_type"] == 11
    assert "10 " in rows[0]["raw_bytes_hex"]

    raw = bytes([0x10, 0x02, 0xB9, 0x25, 0x1C, 0x00, 0x22, 0x00])
    d = _decode_payload(raw)
    assert d["instantaneous_power_w"] == 34
    assert d["cadence_rpm"] == 37
    assert d["left_power_w"] == 15 and d["right_power_w"] == 19

    # BLE CPS Wattbike (da sessione reale): stessi campi ANT-like
    ble = parse_cycling_power_measurement(bytes.fromhex("094f560059720a520018006e000b0300"))
    assert ble["instantaneous_power_w"] == 86
    assert ble["average_power_w"] == 86
    assert ble["right_power_w"] == 38 and ble["left_power_w"] == 48  # 44.5% right
    enrich_kinematics(ble, 75.0)
    assert ble["cadence_rpm"] == 75
    assert ble["torque_nm"] is not None and ble["angular_velocity_rad_s"] is not None
    zero = parse_cycling_power_measurement(bytes.fromhex("094f0000000000000000000000000000"))
    assert zero["instantaneous_power_w"] == 0
    enrich_kinematics(zero, 0.0)
    assert zero["cadence_rpm"] == 0 and zero["left_power_w"] == 0 and zero["right_power_w"] == 0

    stats = timing_stats(rows)
    assert stats["standard_power"]["hz"] == 4.0
    assert is_newer("1.3.1", "1.3.0")
    assert not is_newer("1.3.0", "1.3.0")

    with tempfile.TemporaryDirectory() as tmp:
        xlsx = Path(tmp) / "t.xlsx"
        csv_path = Path(tmp) / "t.csv"
        write_xlsx(xlsx, rows, meta={"mode": "test"})
        write_csv(csv_path, rows)
        assert xlsx.stat().st_size > 0
        header = csv_path.read_text(encoding="utf-8").splitlines()[0]
        assert "event_count" in header
        assert "device_type" in header
        assert "source" in header

    print(f"OK: smoke test v{__version__}")


if __name__ == "__main__":
    main()
