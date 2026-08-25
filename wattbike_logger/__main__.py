"""CLI: scan / record / demo → Excel."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .demo import generate_demo_rows
from .excel_export import write_csv, write_xlsx
from .recorder import SessionRecorder, scan_power_meters, timing_stats


def _default_output(prefix: str = "wattbike") -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(f"{prefix}_{stamp}.xlsx")


def cmd_scan(args: argparse.Namespace) -> int:
    devices = scan_power_meters(timeout_s=args.timeout)
    if not devices:
        print(
            "Nessun PowerMeter trovato.\n"
            "- Accendi la Wattbike e pedala leggermente\n"
            "- Verifica che la chiavetta ANT+ sia inserita\n"
            "- Su Linux: sudo python -m openant.udev_rules  (poi ristacca la chiavetta)\n"
            "- Su Windows: driver libusb-win32 via Zadig (VID 0FCF)"
        )
        return 1
    print(f"\nTrovati {len(devices)} dispositivo/i. Usa --device-id <id> con 'record'.")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    out = Path(args.output) if args.output else _default_output("wattbike")
    recorder = SessionRecorder(
        device_id=args.device_id,
        mode=args.mode,
        quiet=args.quiet,
    )
    try:
        rows = recorder.run(duration_s=args.duration)
    except Exception as exc:
        print(f"Errore ANT+/USB: {exc}", file=sys.stderr)
        print(
            "Controlla chiavetta, driver (Zadig su Windows / udev su Linux) e che nessun altro software usi la stick (Zwift, ecc.).",
            file=sys.stderr,
        )
        return 2

    if not rows:
        print("Nessun pacchetto ricevuto: file non creato.")
        return 1

    stats = timing_stats(rows)
    meta = {
        "mode": f"live/{args.mode}",
        "requested_device_id": args.device_id,
        "found_device_id": recorder.found_device_id,
        "duration_s_requested": args.duration,
        "source": "Wattbike ANT+ Bicycle Power (openant)",
        "timing_stats_json": json.dumps(stats, ensure_ascii=False),
        "power_hz": (stats.get("standard_power") or {}).get("hz"),
        "power_median_dt_s": (stats.get("standard_power") or {}).get("median_dt_s"),
    }
    write_xlsx(out, rows, meta=meta)
    print(f"Salvate {len(rows)} righe → {out.resolve()}")
    if args.csv:
        csv_path = out.with_suffix(".csv")
        write_csv(csv_path, rows)
        print(f"CSV → {csv_path.resolve()}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    out = Path(args.output) if args.output else _default_output("wattbike_demo")
    rows = generate_demo_rows(seconds=args.duration, hz=args.hz, device_id=args.device_id)
    meta = {
        "mode": "demo",
        "device_id": args.device_id,
        "duration_s": args.duration,
        "hz": args.hz,
        "source": "dati simulati (nessuna chiavetta)",
    }
    write_xlsx(out, rows, meta=meta)
    print(f"Demo: {len(rows)} righe → {out.resolve()}")
    if args.csv:
        csv_path = out.with_suffix(".csv")
        write_csv(csv_path, rows)
        print(f"CSV → {csv_path.resolve()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wattbike-logger",
        description="Legge i dati ANT+ raw dalla Wattbike Pro/Trainer e li salva in Excel.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Cerca la Wattbike (PowerMeter ANT+)")
    p_scan.add_argument("--timeout", type=float, default=20.0, help="Secondi di scansione")
    p_scan.set_defaults(func=cmd_scan)

    p_rec = sub.add_parser("record", help="Registra sessione live → Excel")
    p_rec.add_argument(
        "--device-id",
        "-i",
        type=int,
        default=0,
        help="ID dispositivo ANT+ (0 = ascolta qualsiasi PowerMeter)",
    )
    p_rec.add_argument("--output", "-o", type=str, default=None, help="File .xlsx di output")
    p_rec.add_argument(
        "--duration",
        "-d",
        type=float,
        default=None,
        help="Secondi di registrazione (default: finché non premi Ctrl+C)",
    )
    p_rec.add_argument(
        "--mode",
        choices=("scan", "paired"),
        default="scan",
        help="scan=RX continuo (default, più pacchetti); paired=canale PowerMeter classico",
    )
    p_rec.add_argument("--quiet", action="store_true", help="Meno output a schermo")
    p_rec.add_argument("--csv", action="store_true", help="Scrive anche un .csv accanto all'xlsx")
    p_rec.set_defaults(func=cmd_record)

    p_demo = sub.add_parser("demo", help="Genera Excel di prova senza hardware")
    p_demo.add_argument("--output", "-o", type=str, default=None, help="File .xlsx di output")
    p_demo.add_argument("--duration", "-d", type=float, default=30.0, help="Secondi simulati")
    p_demo.add_argument("--hz", type=float, default=4.0, help="Frequenza pacchetti simulati")
    p_demo.add_argument("--device-id", "-i", type=int, default=9999)
    p_demo.add_argument("--csv", action="store_true", help="Scrive anche un .csv")
    p_demo.set_defaults(func=cmd_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
