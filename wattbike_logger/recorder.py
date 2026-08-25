"""Cattura pacchetti ANT+ Power dalla Wattbike e accumula righe raw."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from statistics import mean, median
from typing import Any, Callable, Literal

from openant.devices import ANTPLUS_NETWORK_KEY
from openant.devices.common import DeviceType
from openant.devices.power_meter import PowerMeter
from openant.devices.scanner import Scanner
from openant.easy.channel import Channel
from openant.easy.node import Node

logger = logging.getLogger(__name__)

ReceiveMode = Literal["paired", "scan"]

# ANT+ Bicycle Power channel period (~4.004 Hz → ~0.25 s)
DEFAULT_POWER_PERIOD = 8182


def _bytes_to_hex(data: bytes | list[int] | bytearray) -> str:
    return " ".join(f"{b:02X}" for b in bytes(data[:8]))


def _decode_power_page(data: bytes | list[int] | bytearray) -> dict[str, Any]:
    """Decodifica pagina 0x10 / 0x12 + campi comuni dai 8 byte payload."""
    b = bytes(data[:8])
    page = b[0]
    out: dict[str, Any] = {
        "page": page,
        "event_count": b[1] if page in (0x10, 0x12) else None,
        "instantaneous_power_w": None,
        "average_power_w": None,
        "cadence_rpm": None,
        "left_power_w": None,
        "right_power_w": None,
        "torque_nm": None,
        "angular_velocity_rad_s": None,
        "pedal_power_byte": None,
    }

    if page == 0x10:
        cadence = b[3]
        power = b[6] + (b[7] << 8)
        pedal = b[2]
        out["cadence_rpm"] = None if cadence == 255 else cadence
        out["instantaneous_power_w"] = power
        out["average_power_w"] = power  # istantanea; media accumulata richiede storico
        out["pedal_power_byte"] = pedal
        if pedal != 0xFF and (pedal & 0x80):
            percent = pedal & 0x7F
            right = int((power * percent) / 100)
            out["right_power_w"] = right
            out["left_power_w"] = power - right
        page_name = "standard_power"
    elif page == 0x12:
        cadence = b[3]
        out["cadence_rpm"] = None if cadence == 255 else cadence
        page_name = "standard_torque"
    else:
        page_name = f"page_0x{page:02X}"

    out["page_name"] = page_name
    return out


def make_row(
    *,
    device_id: int,
    raw: bytes | list[int] | bytearray,
    started_at: float,
    decoded: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = time.time()
    decoded = decoded or _decode_power_page(raw)
    return {
        "timestamp_iso": datetime.fromtimestamp(now).isoformat(timespec="milliseconds"),
        "elapsed_s": round(now - started_at, 3),
        "device_id": device_id,
        "page": decoded["page"],
        "page_name": decoded["page_name"],
        "event_count": decoded.get("event_count"),
        "instantaneous_power_w": decoded.get("instantaneous_power_w"),
        "average_power_w": decoded.get("average_power_w"),
        "cadence_rpm": decoded.get("cadence_rpm"),
        "left_power_w": decoded.get("left_power_w"),
        "right_power_w": decoded.get("right_power_w"),
        "torque_nm": decoded.get("torque_nm"),
        "angular_velocity_rad_s": decoded.get("angular_velocity_rad_s"),
        "raw_bytes_hex": _bytes_to_hex(raw),
    }


def timing_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Statistiche intervallo tra pacchetti (tutti e solo power)."""
    def _stats(subset: list[dict[str, Any]], label: str) -> dict[str, Any]:
        elapsed = [float(r["elapsed_s"]) for r in subset]
        if len(elapsed) < 2:
            return {"label": label, "n": len(elapsed), "hz": None, "median_dt_s": None}
        dts = [elapsed[i] - elapsed[i - 1] for i in range(1, len(elapsed))]
        span = elapsed[-1] - elapsed[0]
        return {
            "label": label,
            "n": len(elapsed),
            "hz": round((len(elapsed) - 1) / span, 3) if span > 0 else None,
            "median_dt_s": round(median(dts), 3),
            "mean_dt_s": round(mean(dts), 3),
            "min_dt_s": round(min(dts), 3),
            "max_dt_s": round(max(dts), 3),
        }

    power = [r for r in rows if r.get("page") == 0x10]
    return {
        "all": _stats(rows, "all_pages"),
        "standard_power": _stats(power, "standard_power"),
    }


def print_timing_stats(rows: list[dict[str, Any]]) -> None:
    stats = timing_stats(rows)
    print("\n--- Frequenza ricevuta ---")
    for key in ("all", "standard_power"):
        s = stats[key]
        if s["n"] < 2 or s["hz"] is None:
            print(f"{s['label']}: troppo pochi pacchetti ({s['n']})")
            continue
        print(
            f"{s['label']}: {s['n']} pkt | ~{s['hz']} Hz | "
            f"dt mediano={s['median_dt_s']}s (min={s['min_dt_s']}, max={s['max_dt_s']})"
        )
    print(
        "Nota: ANT+ Bicycle Power standard ≈ 4 Hz (ogni ~0.25 s). "
        "0.20 s richiederebbe ~5 Hz (oltre il tipico ANT+)."
    )


class SessionRecorder:
    """Registra ogni broadcast ANT+ ricevuto come riga raw."""

    def __init__(
        self,
        device_id: int = 0,
        on_row: Callable[[dict[str, Any]], None] | None = None,
        mode: ReceiveMode = "scan",
        quiet: bool = False,
    ):
        self.device_id = device_id
        self.rows: list[dict[str, Any]] = []
        self.on_row = on_row
        self.mode = mode
        self.quiet = quiet
        self.started_at = time.time()
        self.found_device_id: int | None = None
        self.packets_seen = 0

    def _append(self, row: dict[str, Any]) -> None:
        self.rows.append(row)
        self.packets_seen += 1
        if self.on_row:
            self.on_row(row)
        if not self.quiet:
            pwr = row.get("instantaneous_power_w")
            cad = row.get("cadence_rpm")
            ev = row.get("event_count")
            print(
                f"[{row['elapsed_s']:7.1f}s] {row['page_name']:16} "
                f"ev={ev if ev is not None else '-':>3} "
                f"P={pwr if pwr is not None else '-':>4} W  "
                f"cad={cad if cad is not None else '-':>3} rpm  "
                f"raw={row['raw_bytes_hex']}"
            )

    def run(self, duration_s: float | None = None) -> list[dict[str, Any]]:
        if self.mode == "scan":
            return self._run_rx_scan(duration_s)
        return self._run_paired(duration_s)

    def _stop_after(self, node: Node, duration_s: float | None) -> None:
        if duration_s is None:
            return

        def stop_later() -> None:
            time.sleep(duration_s)
            try:
                node.stop()
            except Exception:
                pass

        threading.Thread(target=stop_later, daemon=True).start()

    def _run_paired(self, duration_s: float | None) -> list[dict[str, Any]]:
        """Canale PowerMeter classico (period 8182)."""
        node = Node()
        node.set_network_key(0x00, ANTPLUS_NETWORK_KEY)
        meter = PowerMeter(node, device_id=self.device_id)
        self.started_at = time.time()

        def on_found() -> None:
            self.found_device_id = meter.device_id
            print(f"Connesso (paired) a power meter ANT+ device_id={meter.device_id}")

        original_on_data = meter.on_data

        def on_data_with_raw(data) -> None:
            original_on_data(data)
            # Preferisci decode diretto dai byte (include event_count e L/R).
            decoded = _decode_power_page(data)
            # Se openant ha calcolato average_power più accurata, usala.
            if decoded["page"] == 0x10:
                avg = meter.data["power"].average_power
                if avg:
                    decoded["average_power_w"] = avg
            self._append(
                make_row(
                    device_id=meter.device_id,
                    raw=data,
                    started_at=self.started_at,
                    decoded=decoded,
                )
            )

        meter.on_found = on_found
        meter.on_data = on_data_with_raw

        print(
            "Registrazione PAIRED. Pedala. Ctrl+C per salvare."
            + (f" (auto-stop {duration_s:.0f}s)" if duration_s else "")
        )
        self._stop_after(node, duration_s)
        try:
            node.start()
        except KeyboardInterrupt:
            print("\nInterrotto dall'utente, salvataggio...")
        finally:
            try:
                meter.close_channel()
            except Exception:
                pass
            try:
                node.stop()
            except Exception:
                pass
        print_timing_stats(self.rows)
        return self.rows

    def _run_rx_scan(self, duration_s: float | None) -> list[dict[str, Any]]:
        """
        RX scan mode: radio in ricezione continua (duty cycle 100%).
        Cattura ogni broadcast sulla freq ANT+ indipendentemente dal period,
        tipicamente più vicino ai ~4 Hz se la Wattbike li trasmette.
        """
        node = Node()
        node.set_network_key(0x00, ANTPLUS_NETWORK_KEY)
        self.started_at = time.time()

        channel = node.new_channel(
            Channel.Type.BIDIRECTIONAL_RECEIVE, 0x00, 0x01  # extended assign
        )
        # Wildcard su device_id se 0; filtra a runtime.
        channel.set_id(self.device_id, DeviceType.PowerMeter.value, 0)
        channel.enable_extended_messages(1)
        channel.set_period(DEFAULT_POWER_PERIOD)
        channel.set_rf_freq(57)
        channel.set_search_timeout(0xFF)

        def on_data(data) -> None:
            # extended: bytes 9-12 = device_id lo, hi, type, trans
            device_id = self.device_id
            if len(data) > 8:
                device_id = data[9] + (data[10] << 8)
                device_type = data[11]
                if device_type != DeviceType.PowerMeter.value:
                    return
                if self.device_id and device_id != self.device_id:
                    return
            if self.found_device_id is None:
                self.found_device_id = device_id
                print(f"Ricezione (rx-scan) da power meter device_id={device_id}")

            self._append(
                make_row(device_id=device_id, raw=data, started_at=self.started_at)
            )

        channel.on_broadcast_data = on_data
        channel.on_burst_data = on_data
        channel.on_acknowledge = on_data

        print(
            "Registrazione RX-SCAN (ascolto continuo). Pedala. Ctrl+C per salvare."
            + (f" (auto-stop {duration_s:.0f}s)" if duration_s else "")
        )
        print("Obiettivo tipico ANT+: ~4 Hz (ogni ~0.25 s).")

        self._stop_after(node, duration_s)
        try:
            # open_rx_scan_mode invece di channel.open(): RX al 100%
            channel.open_rx_scan_mode()
            node.start()
        except KeyboardInterrupt:
            print("\nInterrotto dall'utente, salvataggio...")
        finally:
            try:
                node.remove_channel(channel)
            except Exception:
                pass
            try:
                node.stop()
            except Exception:
                pass
        print_timing_stats(self.rows)
        return self.rows


def scan_power_meters(timeout_s: float = 20.0) -> list[dict[str, Any]]:
    """Scansiona dispositivi ANT+ PowerMeter (tipo 11) e restituisce gli ID trovati."""
    found: dict[tuple[int, int, int], dict[str, Any]] = {}
    node = Node()
    node.set_network_key(0x00, ANTPLUS_NETWORK_KEY)
    scanner = Scanner(node, device_id=0, device_type=DeviceType.PowerMeter.value)

    def on_found(device_tuple) -> None:
        device_id, device_type, device_trans = device_tuple
        key = (device_id, device_type, device_trans)
        if key in found:
            return
        info = {
            "device_id": device_id,
            "device_type": device_type,
            "device_type_name": DeviceType(device_type).name,
            "transmission_type": device_trans,
        }
        found[key] = info
        print(
            f"Trovato: device_id={device_id}  type={DeviceType(device_type).name} "
            f"({device_type})  trans={device_trans}"
        )

    scanner.on_found = on_found
    print(f"Scansione PowerMeter ANT+ per {timeout_s:.0f}s... Accendi la Wattbike.")

    def stop_later() -> None:
        time.sleep(timeout_s)
        try:
            node.stop()
        except Exception:
            pass

    threading.Thread(target=stop_later, daemon=True).start()
    try:
        node.start()
    except KeyboardInterrupt:
        print("\nScansione interrotta.")
    finally:
        try:
            scanner.close_channel()
        except Exception:
            pass
        try:
            node.stop()
        except Exception:
            pass

    return list(found.values())
