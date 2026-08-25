#!/usr/bin/env python3
"""Smoke test senza hardware ANT+."""

from pathlib import Path
import tempfile

from wattbike_logger.demo import generate_demo_rows
from wattbike_logger.excel_export import COLUMNS, write_csv, write_xlsx
from wattbike_logger.recorder import _decode_power_page, timing_stats


def main() -> None:
    rows = generate_demo_rows(seconds=2, hz=4)
    assert len(rows) == 8
    assert all(c in rows[0] for c in COLUMNS)
    assert rows[0]["page"] == 0x10
    assert rows[0]["event_count"] == 1
    assert "10 " in rows[0]["raw_bytes_hex"]

    raw = bytes([0x10, 0x02, 0xB9, 0x25, 0x1C, 0x00, 0x22, 0x00])
    d = _decode_power_page(raw)
    assert d["instantaneous_power_w"] == 34
    assert d["cadence_rpm"] == 37
    assert d["left_power_w"] == 15 and d["right_power_w"] == 19

    stats = timing_stats(rows)
    assert stats["standard_power"]["hz"] == 4.0

    with tempfile.TemporaryDirectory() as tmp:
        xlsx = Path(tmp) / "t.xlsx"
        csv = Path(tmp) / "t.csv"
        write_xlsx(xlsx, rows, meta={"mode": "test"})
        write_csv(csv, rows)
        assert xlsx.stat().st_size > 0
        assert "event_count" in csv.read_text(encoding="utf-8").splitlines()[0]

    print("OK: demo + decode + export Excel/CSV")


if __name__ == "__main__":
    main()
