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

# Profili trainer su 2457 MHz: solo quelli con potenza/cadenza utile
INTERESTING_DEVICE_TYPES = {
    DeviceType.PowerMeter.value,
    DeviceType.FitnessEquipment.value,
}

# Pagine con watt/cadenza (esclude 0x50/0x51 manufacturer e S&C grezzo)
DATA_PAGES = {0x10, 0x19}


def _bytes_to_hex(data: bytes | list[int] | bytearray) -> str:
    return " ".join(f"{b:02X}" for b in bytes(data[:8]))


def _device_type_name(device_type: int | None) -> str | None:
    if device_type is None:
        return None
    try:
        return DeviceType(device_type).name
    except ValueError:
        return f"type_{device_type}"


def _decode_payload(
    data: bytes | list[int] | bytearray,
    device_type: int | None = None,
) -> dict[str, Any]:
    """Decodifica pagine power (0x10/0x12) e FE-C trainer power (0x19)."""
    b = bytes(data[:8])
    page = b[0]
    out: dict[str, Any] = {
        "page": page,
        "event_count": None,
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
        out["event_count"] = b[1]
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
        out["event_count"] = b[1]
        cadence = b[3]
        out["cadence_rpm"] = None if cadence == 255 else cadence
        page_name = "standard_torque"
    elif page == 0x19:
        # FE-C specific trainer data (bike power)
        out["event_count"] = b[1]
        cadence = b[2]
        out["cadence_rpm"] = None if cadence == 255 else cadence
        out["average_power_w"] = b[3] + (b[4] << 8)
        out["instantaneous_power_w"] = b[5] + ((b[6] & 0x0F) << 8)
        page_name = "fe_trainer_power"
    else:
        if device_type == DeviceType.FitnessEquipment.value:
            page_name = f"fe_page_0x{page:02X}"
        else:
            page_name = f"page_0x{page:02X}"

    out["page_name"] = page_name
    return out


# Compat alias
_decode_power_page = _decode_payload


def row_has_metrics(row: dict[str, Any]) -> bool:
    """True se la riga ha potenza (e di solito cadenza) usabile in analisi."""
    return row.get("instantaneous_power_w") is not None and row.get("page") in DATA_PAGES


def filter_metric_rows(
    rows: list[dict[str, Any]],
    *,
    dedupe_events: bool = True,
) -> list[dict[str, Any]]:
    """
    Tiene solo pagine power/FE con watt.
    Se dedupe_events: scarta ritrasmissioni ANT dello stesso event_count.
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for row in rows:
        if not row_has_metrics(row):
            continue
        if dedupe_events:
            key = (row.get("device_id"), row.get("page"), row.get("event_count"))
            if key in seen:
                continue
            seen.add(key)
        out.append(row)
    return out


def make_row(
    *,
    device_id: int,
    raw: bytes | list[int] | bytearray,
    started_at: float,
    decoded: dict[str, Any] | None = None,
    device_type: int | None = None,
) -> dict[str, Any]:
    now = time.time()
    decoded = decoded or _decode_payload(raw, device_type=device_type)
    return {
        "timestamp_iso": datetime.fromtimestamp(now).isoformat(timespec="milliseconds"),
        "elapsed_s": round(now - started_at, 3),
        "device_id": device_id,
        "device_type": device_type,
        "device_type_name": _device_type_name(device_type),
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
    fe_power = [r for r in rows if r.get("page") == 0x19]
    unique = filter_metric_rows(rows, dedupe_events=True)
    return {
        "all": _stats(rows, "all_pages"),
        "standard_power": _stats(power, "standard_power"),
        "fe_trainer_power": _stats(fe_power, "fe_trainer_power"),
        "unique_power_events": _stats(unique, "unique_power_events"),
    }


def print_timing_stats(rows: list[dict[str, Any]]) -> None:
    stats = timing_stats(rows)
    print("\n--- Frequenza ricevuta ---")
    for key in ("all", "standard_power", "fe_trainer_power", "unique_power_events"):
        s = stats[key]
        if s["n"] == 0:
            continue
        if s["n"] < 2 or s["hz"] is None:
            print(f"{s['label']}: troppo pochi pacchetti ({s['n']})")
            continue
        print(
            f"{s['label']}: {s['n']} pkt | ~{s['hz']} Hz | "
            f"dt mediano={s['median_dt_s']}s (min={s['min_dt_s']}, max={s['max_dt_s']})"
        )
    print(
        "Nota: ANT+ Bicycle Power standard ≈ 4 Hz (ogni ~0.25 s). "
        "unique_power_events scarta le ritrasmissioni dello stesso event_count."
    )


class SessionRecorder:
    """Registra ogni broadcast ANT+ ricevuto come riga raw."""

    def __init__(
        self,
        device_id: int = 0,
        on_row: Callable[[dict[str, Any]], None] | None = None,
        mode: ReceiveMode = "scan",
        quiet: bool = False,
        metrics_only: bool = True,
        dedupe_events: bool = False,
    ):
        self.device_id = device_id
        self.rows: list[dict[str, Any]] = []
        self.on_row = on_row
        self.mode = mode
        self.quiet = quiet
        self.metrics_only = metrics_only
        self.dedupe_events = dedupe_events
        self.started_at = time.time()
        self.found_device_id: int | None = None
        self.packets_seen = 0
        self._seen_events: set[tuple[Any, Any, Any]] = set()

    def _append(self, row: dict[str, Any]) -> None:
        if self.metrics_only and not row_has_metrics(row):
            return
        if self.dedupe_events and row_has_metrics(row):
            key = (row.get("device_id"), row.get("page"), row.get("event_count"))
            if key in self._seen_events:
                return
            self._seen_events.add(key)
        self.rows.append(row)
        self.packets_seen += 1
        if self.on_row:
            self.on_row(row)
        if not self.quiet:
            pwr = row.get("instantaneous_power_w")
            cad = row.get("cadence_rpm")
            ev = row.get("event_count")
            dtype = row.get("device_type_name") or "?"
            print(
                f"[{row['elapsed_s']:7.1f}s] {dtype:16} {row['page_name']:18} "
                f"ev={ev if ev is not None else '-':>3} "
                f"P={pwr if pwr is not None else '-':>4} W  "
                f"cad={cad if cad is not None else '-':>3} rpm  "
                f"raw={row['raw_bytes_hex']}"
            )

    def run(self, duration_s: float | None = None) -> list[dict[str, Any]]:
        if self.mode == "scan":
            try:
                return self._run_rx_scan(duration_s)
            except Exception as exc:
                print(
                    f"RX-scan non disponibile ({exc}). Fallback a modalità paired...",
                    flush=True,
                )
                # Nuova sessione pulita: la stick potrebbe essere in stato inconsistente.
                time.sleep(0.5)
                return self._run_paired(duration_s)
        return self._run_paired(duration_s)

    @staticmethod
    def _safe_stop(node: Node | None, timeout_s: float = 2.0) -> None:
        """Ferma Node/USB senza hang infiniti (evita segfault su Ctrl+C)."""
        if node is None:
            return

        def _force() -> None:
            try:
                node._running = False  # type: ignore[attr-defined]
            except Exception:
                pass
            ant = getattr(node, "ant", None)
            if ant is not None:
                try:
                    ant._running = False
                except Exception:
                    pass
                try:
                    ant._driver.close()
                except Exception:
                    pass
            for obj in (node, ant):
                if obj is None:
                    continue
                thr = getattr(obj, "_worker_thread", None)
                if thr is not None and thr.is_alive():
                    thr.join(timeout_s)

        stopper = threading.Thread(target=_force, daemon=True)
        stopper.start()
        stopper.join(timeout_s + 0.5)

    def _stop_after(self, node: Node, duration_s: float | None) -> None:
        if duration_s is None:
            return

        def stop_later() -> None:
            time.sleep(duration_s)
            # Sblocca node.start()/_main senza join infinito sul worker USB.
            try:
                node._running = False  # type: ignore[attr-defined]
            except Exception:
                pass
            ant = getattr(node, "ant", None)
            if ant is not None:
                try:
                    ant._running = False
                except Exception:
                    pass

        threading.Thread(target=stop_later, daemon=True).start()

    def _run_paired(self, duration_s: float | None) -> list[dict[str, Any]]:
        """Canale PowerMeter classico (period 8182) — path affidabile."""
        node = Node()
        meter: PowerMeter | None = None
        try:
            node.set_network_key(0x00, ANTPLUS_NETWORK_KEY)
            meter = PowerMeter(node, device_id=self.device_id)
            self.started_at = time.time()

            def on_found() -> None:
                self.found_device_id = meter.device_id
                print(f"Connesso (paired) a power meter ANT+ device_id={meter.device_id}")

            original_on_data = meter.on_data

            def on_data_with_raw(data) -> None:
                original_on_data(data)
                decoded = _decode_payload(data, device_type=DeviceType.PowerMeter.value)
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
                        device_type=DeviceType.PowerMeter.value,
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
            # Evita close_channel/remove_channel: in errore USB fanno altri timeout da ~10s.
            self._safe_stop(node)
        print_timing_stats(self.rows)
        return self.rows

    def _run_rx_scan(self, duration_s: float | None) -> list[dict[str, Any]]:
        """
        RX scan mode: radio in ricezione continua (duty cycle 100%).
        Se l'apertura fallisce/timeout, run() fa fallback a paired.
        """
        node = Node()
        channel: Channel | None = None
        try:
            node.set_network_key(0x00, ANTPLUS_NETWORK_KEY)
            self.started_at = time.time()
            seen_keys: set[tuple[int, int]] = set()

            channel = node.new_channel(
                Channel.Type.BIDIRECTIONAL_RECEIVE, 0x00, 0x01  # extended assign
            )
            # Solo PowerMeter (+ FE se stesso stream): meno rumore, setup più stabile.
            channel.set_id(
                self.device_id if self.device_id else 0,
                DeviceType.PowerMeter.value,
                0,
            )
            channel.enable_extended_messages(1)
            channel.set_period(DEFAULT_POWER_PERIOD)
            channel.set_rf_freq(57)
            channel.set_search_timeout(0xFF)

            def on_data(data) -> None:
                device_id = self.device_id or 0
                device_type: int | None = DeviceType.PowerMeter.value
                if len(data) > 8:
                    device_id = data[9] + (data[10] << 8)
                    device_type = data[11]
                    if device_type not in INTERESTING_DEVICE_TYPES:
                        return
                    if (
                        self.device_id
                        and device_type == DeviceType.PowerMeter.value
                        and device_id != self.device_id
                    ):
                        return
                key = (device_id, device_type if device_type is not None else -1)
                if key not in seen_keys:
                    seen_keys.add(key)
                    print(
                        f"Ricezione (rx-scan) device_id={device_id} "
                        f"type={_device_type_name(device_type)} ({device_type})"
                    )
                if self.found_device_id is None:
                    self.found_device_id = device_id

                self._append(
                    make_row(
                        device_id=device_id,
                        raw=data,
                        started_at=self.started_at,
                        device_type=device_type,
                    )
                )

            channel.on_broadcast_data = on_data
            channel.on_burst_data = on_data
            channel.on_acknowledge = on_data

            print(
                "Registrazione RX-SCAN (solo pagine potenza). "
                "Pedala. Ctrl+C per salvare."
                + (f" (auto-stop {duration_s:.0f}s)" if duration_s else "")
            )

            channel.open_rx_scan_mode()
            self._stop_after(node, duration_s)
            try:
                node.start()
            except KeyboardInterrupt:
                print("\nInterrotto dall'utente, salvataggio...")
        except Exception:
            self._safe_stop(node)
            raise
        finally:
            self._safe_stop(node)
        print_timing_stats(self.rows)
        return self.rows


def scan_power_meters(timeout_s: float = 20.0) -> list[dict[str, Any]]:
    """Scansiona dispositivi ANT+ PowerMeter (tipo 11) e restituisce gli ID trovati."""
    found: dict[tuple[int, int, int], dict[str, Any]] = {}
    node = Node()
    try:
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
                node._running = False  # type: ignore[attr-defined]
            except Exception:
                pass
            ant = getattr(node, "ant", None)
            if ant is not None:
                try:
                    ant._running = False
                except Exception:
                    pass

        threading.Thread(target=stop_later, daemon=True).start()
        try:
            node.start()
        except KeyboardInterrupt:
            print("\nScansione interrotta.")
    finally:
        SessionRecorder._safe_stop(node)

    return list(found.values())
