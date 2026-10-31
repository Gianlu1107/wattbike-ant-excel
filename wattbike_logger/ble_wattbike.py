"""Wattbike Pro/Trainer via Bluetooth LE (CPS + CSC, come Hub/Zwift)."""

from __future__ import annotations

import asyncio
import re
import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

# Standard BLE Fitness
UUID_CPS = "00001818-0000-1000-8000-00805f9b34fb"
UUID_CSC = "00001816-0000-1000-8000-00805f9b34fb"
UUID_CPM = "00002a63-0000-1000-8000-00805f9b34fb"
UUID_CSC_MEAS = "00002a5b-0000-1000-8000-00805f9b34fb"
# Wattbike monitor proprietary (Hub / Polar View — protocollo non documentato)
UUID_WB_SVC = "f7461223-d7c1-11e4-9ab1-0002a5d5c51b"
UUID_WB_NOTIFY = "f7461224-d7c1-11e4-9ab1-0002a5d5c51b"

StatusFn = Callable[[str], None]
RowFn = Callable[[dict[str, Any]], None]


@dataclass
class BleDeviceInfo:
    address: str
    name: str
    rssi: int | None = None
    ant_device_id: int | None = None  # es. WattbikePM25054434 → 54434


def _parse_ant_id_from_name(name: str) -> int | None:
    # WattbikePM25054434 / WattbikePM54434
    m = re.search(r"(\d{4,6})$", name.strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def bleak_available() -> bool:
    try:
        import bleak  # noqa: F401

        return True
    except ImportError:
        return False


async def discover_wattbikes(timeout_s: float = 8.0) -> list[BleDeviceInfo]:
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout_s, return_adv=True)
    out: list[BleDeviceInfo] = []
    for addr, (dev, adv) in found.items():
        name = (dev.name or adv.local_name or "") or ""
        low = name.lower()
        uuids = [str(u).lower() for u in (adv.service_uuids or [])]
        is_wb = "wattbike" in low
        has_cps = any("1818" in u for u in uuids)
        has_wb_svc = any("f7461223" in u for u in uuids)
        if not (is_wb or (has_cps and has_wb_svc)):
            continue
        out.append(
            BleDeviceInfo(
                address=addr,
                name=name or addr,
                rssi=adv.rssi,
                ant_device_id=_parse_ant_id_from_name(name),
            )
        )
    # Preferisci nome Wattbike
    out.sort(key=lambda d: (0 if "wattbike" in d.name.lower() else 1, -(d.rssi or -999)))
    return out


def discover_wattbikes_sync(timeout_s: float = 8.0) -> list[BleDeviceInfo]:
    return asyncio.run(discover_wattbikes(timeout_s))


def parse_cycling_power_measurement(data: bytes) -> dict[str, Any]:
    """Decodifica CPS Cycling Power Measurement (little-endian)."""
    if len(data) < 4:
        return {}
    flags = struct.unpack_from("<H", data, 0)[0]
    power = struct.unpack_from("<h", data, 2)[0]
    off = 4
    out: dict[str, Any] = {
        "flags": flags,
        "instantaneous_power_w": int(power),
        "page": "ble_cps",
        "page_name": "ble_cycling_power",
    }
    if flags & 0x01 and off < len(data):  # pedal power balance
        out["pedal_balance_pct"] = data[off] / 2.0
        off += 1
    if flags & 0x04 and off + 2 <= len(data):  # accumulated torque
        out["accum_torque"] = struct.unpack_from("<H", data, off)[0]
        off += 2
    if flags & 0x10 and off + 6 <= len(data):  # wheel revs
        revs, t = struct.unpack_from("<IH", data, off)
        out["wheel_revs"] = revs
        out["wheel_event_time"] = t
        off += 6
    if flags & 0x20 and off + 4 <= len(data):  # crank revs
        revs, t = struct.unpack_from("<HH", data, off)
        out["crank_revs"] = revs
        out["crank_event_time"] = t
    return out


def cadence_from_crank(
    prev: tuple[int, int] | None, revs: int, event_time: int
) -> tuple[float | None, tuple[int, int]]:
    """Cadenza da crank revs / event time (1/1024 s)."""
    cur = (revs, event_time)
    if prev is None:
        return None, cur
    d_revs = (revs - prev[0]) & 0xFFFF
    d_t = (event_time - prev[1]) & 0xFFFF
    if d_t == 0 or d_revs == 0:
        return None, cur
    rpm = 60.0 * d_revs / (d_t / 1024.0)
    if rpm < 0 or rpm > 300:
        return None, cur
    return round(rpm, 1), cur


def make_ble_row(
    *,
    device_id: int,
    raw: bytes,
    started_at: float,
    decoded: dict[str, Any],
    source_detail: str = "ble_cps",
) -> dict[str, Any]:
    now = time.time()
    return {
        "timestamp_iso": datetime.fromtimestamp(now).isoformat(timespec="milliseconds"),
        "elapsed_s": round(now - started_at, 3),
        "device_id": device_id,
        "device_type": None,
        "device_type_name": "WattbikeBLE",
        "page": decoded.get("page", "ble_cps"),
        "page_name": decoded.get("page_name", "ble_cycling_power"),
        "event_count": decoded.get("crank_revs") or decoded.get("event_count"),
        "instantaneous_power_w": decoded.get("instantaneous_power_w"),
        "average_power_w": decoded.get("average_power_w"),
        "cadence_rpm": decoded.get("cadence_rpm"),
        "left_power_w": decoded.get("left_power_w"),
        "right_power_w": decoded.get("right_power_w"),
        "torque_nm": decoded.get("torque_nm"),
        "angular_velocity_rad_s": decoded.get("angular_velocity_rad_s"),
        "raw_bytes_hex": raw.hex(),
        "source": "ble",
        "source_detail": source_detail,
    }


class BleWattbikeSession:
    """
    Connessione BLE in thread dedicato (asyncio).
    Compatibile con LiveSession ANT+: stesso on_row / capturing.
    """

    def __init__(
        self,
        address: str | None = None,
        name_substr: str = "Wattbike",
        device_id: int = 0,
        on_row: RowFn | None = None,
        on_status: StatusFn | None = None,
        include_proprietary: bool = True,
    ):
        self.address = address
        self.name_substr = name_substr
        self.device_id = device_id
        self.on_row = on_row
        self.on_status = on_status
        self.include_proprietary = include_proprietary
        self.capturing = False
        self.connected = False
        self.resolved_name: str | None = None
        self.resolved_address: str | None = None
        self.resolved_device_id: int | None = None
        self._started_at = time.time()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error: str | None = None
        self._crank_prev: tuple[int, int] | None = None
        self._lock = threading.Lock()

    def _status(self, msg: str) -> None:
        if self.on_status:
            self.on_status(msg)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._thread_main, daemon=True, name="ble-wattbike")
        self._thread.start()
        # Attendi connect o timeout
        self._ready.wait(timeout=25.0)
        if self._error and not self.connected:
            raise RuntimeError(self._error)

    def begin_capture(self) -> None:
        with self._lock:
            self._started_at = time.time()
            self._crank_prev = None
            self.capturing = True

    def stop(self) -> None:
        with self._lock:
            self.capturing = False
        self._stop.set()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(lambda: None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._thread = None
        self.connected = False

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception as exc:
            self._error = str(exc)
            self._status(f"BLE errore: {exc}")
        finally:
            self.connected = False
            self._ready.set()

    async def _async_main(self) -> None:
        from bleak import BleakClient, BleakScanner

        self._loop = asyncio.get_running_loop()
        addr = self.address
        name = None
        if not addr:
            self._status("Scansione Bluetooth Wattbike...")
            devices = await discover_wattbikes(10.0)
            if not devices:
                # fallback: qualsiasi nome con substr
                raw = await BleakScanner.discover(timeout=6.0, return_adv=True)
                for a, (dev, adv) in raw.items():
                    n = (dev.name or adv.local_name or "") or ""
                    if self.name_substr.lower() in n.lower():
                        devices.append(
                            BleDeviceInfo(a, n, adv.rssi, _parse_ant_id_from_name(n))
                        )
                        break
            if not devices:
                self._error = "Nessuna Wattbike BLE trovata"
                self._ready.set()
                return
            pick = devices[0]
            addr = pick.address
            name = pick.name
            if self.device_id == 0 and pick.ant_device_id:
                self.resolved_device_id = pick.ant_device_id
            self._status(f"BLE: {pick.name} (RSSI {pick.rssi})")
        self.resolved_address = addr
        self.resolved_name = name

        def emit(row: dict[str, Any]) -> None:
            with self._lock:
                if not self.capturing:
                    return
            if self.on_row:
                self.on_row(row)

        def on_cpm(_handle: int, data: bytearray) -> None:
            raw = bytes(data)
            decoded = parse_cycling_power_measurement(raw)
            if not decoded:
                return
            if "crank_revs" in decoded and "crank_event_time" in decoded:
                cad, self._crank_prev = cadence_from_crank(
                    self._crank_prev,
                    int(decoded["crank_revs"]),
                    int(decoded["crank_event_time"]),
                )
                if cad is not None:
                    decoded["cadence_rpm"] = cad
            did = self.device_id or self.resolved_device_id or 0
            emit(
                make_ble_row(
                    device_id=did,
                    raw=raw,
                    started_at=self._started_at,
                    decoded=decoded,
                )
            )

        def on_prop(_handle: int, data: bytearray) -> None:
            raw = bytes(data)
            did = self.device_id or self.resolved_device_id or 0
            emit(
                make_ble_row(
                    device_id=did,
                    raw=raw,
                    started_at=self._started_at,
                    decoded={
                        "page": "ble_prop",
                        "page_name": "ble_wattbike_proprietary",
                        "instantaneous_power_w": None,
                    },
                    source_detail="ble_proprietary",
                )
            )

        self._status(f"Connessione BLE a {addr}...")
        async with BleakClient(addr, timeout=20.0) as client:
            self.connected = True
            self._status("Bluetooth Wattbike connesso (CPS)")
            self._ready.set()
            await client.start_notify(UUID_CPM, on_cpm)
            try:
                await client.start_notify(UUID_CSC_MEAS, lambda h, d: None)
            except Exception:
                pass
            if self.include_proprietary:
                try:
                    await client.start_notify(UUID_WB_NOTIFY, on_prop)
                except Exception as exc:
                    self._status(f"BLE proprietario non sottoscritto: {exc}")

            while not self._stop.is_set():
                if not client.is_connected:
                    self._error = "BLE disconnesso"
                    break
                await asyncio.sleep(0.2)

            for uuid in (UUID_CPM, UUID_CSC_MEAS, UUID_WB_NOTIFY):
                try:
                    await client.stop_notify(uuid)
                except Exception:
                    pass
