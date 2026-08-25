"""Cattura pacchetti ANT+ Power dalla Wattbike e accumula righe raw."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable

from openant.devices import ANTPLUS_NETWORK_KEY
from openant.devices.common import DeviceType
from openant.devices.power_meter import PowerMeter
from openant.devices.scanner import Scanner
from openant.easy.node import Node

logger = logging.getLogger(__name__)


def _bytes_to_hex(data: bytes | list[int] | bytearray) -> str:
    return " ".join(f"{b:02X}" for b in bytes(data[:8]))


def power_row(
    *,
    device_id: int,
    page: int,
    page_name: str,
    power_data: Any,
    raw: bytes | list[int] | bytearray,
    started_at: float,
) -> dict[str, Any]:
    now = time.time()
    cadence = getattr(power_data, "cadence", None)
    if cadence == 255:
        cadence = None

    left = getattr(power_data, "left_power", None)
    right = getattr(power_data, "right_power", None)
    if left == -1:
        left = None
    if right == -1:
        right = None

    return {
        "timestamp_iso": datetime.fromtimestamp(now).isoformat(timespec="milliseconds"),
        "elapsed_s": round(now - started_at, 3),
        "device_id": device_id,
        "page": page,
        "page_name": page_name,
        "instantaneous_power_w": getattr(power_data, "instantaneous_power", None),
        "average_power_w": getattr(power_data, "average_power", None),
        "cadence_rpm": cadence,
        "left_power_w": left,
        "right_power_w": right,
        "torque_nm": getattr(power_data, "torque", None),
        "angular_velocity_rad_s": getattr(power_data, "angular_velocity", None),
        "raw_bytes_hex": _bytes_to_hex(raw),
    }


class SessionRecorder:
    """Registra ogni aggiornamento pagina ANT+ come riga raw."""

    def __init__(self, device_id: int = 0, on_row: Callable[[dict[str, Any]], None] | None = None):
        self.device_id = device_id
        self.rows: list[dict[str, Any]] = []
        self.on_row = on_row
        self.started_at = time.time()
        self.found_device_id: int | None = None
        self._node: Node | None = None
        self._meter: PowerMeter | None = None
        self._original_on_data = None

    def _append(self, row: dict[str, Any]) -> None:
        self.rows.append(row)
        if self.on_row:
            self.on_row(row)

    def run(self, duration_s: float | None = None) -> list[dict[str, Any]]:
        node = Node()
        node.set_network_key(0x00, ANTPLUS_NETWORK_KEY)
        self._node = node

        meter = PowerMeter(node, device_id=self.device_id)
        self._meter = meter
        self.started_at = time.time()

        def on_found() -> None:
            self.found_device_id = meter.device_id
            logger.info("Wattbike / power meter trovato: device_id=%s", meter.device_id)
            print(f"Connesso a power meter ANT+ device_id={meter.device_id}")

        def on_device_data(page: int, page_name: str, data) -> None:
            # I byte raw arrivano dal wrapper su on_data.
            pass

        # Intercetta i byte grezzi della pagina ANT+ (8 byte payload).
        original_on_data = meter.on_data

        def on_data_with_raw(data) -> None:
            page = data[0]
            original_on_data(data)
            power = meter.data["power"]
            page_name = {
                0x10: "standard_power",
                0x12: "standard_torque",
            }.get(page, f"page_0x{page:02X}")
            # Per pagine non power, salva comunque il payload grezzo.
            if page not in (0x10, 0x12):
                row = {
                    "timestamp_iso": datetime.now().isoformat(timespec="milliseconds"),
                    "elapsed_s": round(time.time() - self.started_at, 3),
                    "device_id": meter.device_id,
                    "page": page,
                    "page_name": page_name,
                    "instantaneous_power_w": None,
                    "average_power_w": None,
                    "cadence_rpm": None,
                    "left_power_w": None,
                    "right_power_w": None,
                    "torque_nm": None,
                    "angular_velocity_rad_s": None,
                    "raw_bytes_hex": _bytes_to_hex(data),
                }
            else:
                row = power_row(
                    device_id=meter.device_id,
                    page=page,
                    page_name=page_name,
                    power_data=power,
                    raw=data,
                    started_at=self.started_at,
                )
            self._append(row)
            pwr = row.get("instantaneous_power_w")
            cad = row.get("cadence_rpm")
            print(
                f"[{row['elapsed_s']:7.1f}s] page={page_name:16} "
                f"P={pwr if pwr is not None else '-':>4} W  "
                f"cad={cad if cad is not None else '-':>3} rpm  "
                f"raw={row['raw_bytes_hex']}"
            )

        meter.on_found = on_found
        meter.on_device_data = on_device_data
        meter.on_data = on_data_with_raw

        deadline = None if duration_s is None else time.time() + duration_s
        print(
            "Registrazione in corso. Pedala sulla Wattbike. Ctrl+C per terminare e salvare."
            + (f" (auto-stop tra {duration_s:.0f}s)" if duration_s else "")
        )
        try:
            if deadline is None:
                node.start()
            else:
                # Avvio node in thread via start(), poi attendi durata.
                # node.start() è bloccante: usiamo un timer interno con stop.
                import threading

                def stop_later() -> None:
                    remaining = deadline - time.time()
                    if remaining > 0:
                        time.sleep(remaining)
                    try:
                        node.stop()
                    except Exception:
                        pass

                threading.Thread(target=stop_later, daemon=True).start()
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

    import threading

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
