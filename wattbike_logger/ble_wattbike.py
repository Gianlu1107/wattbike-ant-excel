"""Wattbike Pro/Trainer via Bluetooth LE (CPS + CSC, come Hub/Zwift)."""

from __future__ import annotations

import asyncio
import math
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
    out.sort(key=lambda d: (0 if "wattbike" in d.name.lower() else 1, -(d.rssi or -999)))
    return out


def discover_wattbikes_sync(timeout_s: float = 8.0) -> list[BleDeviceInfo]:
    return asyncio.run(discover_wattbikes(timeout_s))


def _bytes_to_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def cadence_from_crank(
    prev: tuple[int, int] | None, revs: int, event_time: int
) -> tuple[float | None, tuple[int, int]]:
    """Cadenza da crank revs / event time (1/1024 s). d_revs==0 → 0 rpm (fermo)."""
    cur = (revs, event_time)
    if prev is None:
        return None, cur
    d_revs = (revs - prev[0]) & 0xFFFF
    d_t = (event_time - prev[1]) & 0xFFFF
    if d_t == 0:
        return 0.0 if d_revs == 0 else None, cur
    if d_revs == 0:
        return 0.0, cur
    rpm = 60.0 * d_revs / (d_t / 1024.0)
    if rpm < 0 or rpm > 300:
        return None, cur
    return round(rpm, 1), cur


def parse_csc_measurement(data: bytes) -> dict[str, Any]:
    if not data:
        return {}
    flags = data[0]
    off = 1
    out: dict[str, Any] = {"flags": flags}
    if flags & 0x01 and off + 6 <= len(data):
        revs, t = struct.unpack_from("<IH", data, off)
        out["wheel_revs"] = revs
        out["wheel_event_time"] = t
        off += 6
    if flags & 0x02 and off + 4 <= len(data):
        revs, t = struct.unpack_from("<HH", data, off)
        out["crank_revs"] = revs
        out["crank_event_time"] = t
    return out


def parse_cycling_power_measurement(data: bytes) -> dict[str, Any]:
    """
    Decodifica CPS → stessi campi usati dalle righe ANT+ (watt, cadenza, L/R, …).
    Wattbike Pro/Trainer espone tipicamente flags 0x4F09 (PPB + angoli + energy).
    """
    if len(data) < 4:
        return {}
    flags = struct.unpack_from("<H", data, 0)[0]
    power = int(struct.unpack_from("<h", data, 2)[0])
    # Clamp rumorosi negativi
    if power < 0:
        power = 0
    off = 4
    out: dict[str, Any] = {
        "flags": flags,
        "page": "ble_cps",
        "page_name": "ble_cycling_power",
        "instantaneous_power_w": power,
        "average_power_w": power,
        "cadence_rpm": None,
        "left_power_w": None,
        "right_power_w": None,
        "torque_nm": None,
        "angular_velocity_rad_s": None,
        "event_count": None,
    }

    if flags & 0x01 and off < len(data):  # Pedal Power Balance Present
        bal_raw = data[off]
        off += 1
        pct = bal_raw / 2.0  # unità 1/2 %
        out["pedal_balance_pct"] = pct
        # Wattbike BLE balance è specchiato rispetto ad ANT page 0x10 (verificato dual capture).
        # Mappiamo L/R come ANT: pedale % sul destro (byte ANT bit7), lato opposto = resto.
        if power == 0:
            out["left_power_w"] = 0
            out["right_power_w"] = 0
        else:
            # Empirico vs dual capture: % BLE ≈ sinistro ANT (non destro SIG "naive").
            left = int(round(power * pct / 100.0))
            out["left_power_w"] = left
            out["right_power_w"] = power - left

    if flags & 0x04 and off + 2 <= len(data):  # Accumulated Torque
        out["accum_torque"] = struct.unpack_from("<H", data, off)[0]
        off += 2

    if flags & 0x10 and off + 6 <= len(data):  # Wheel revs
        revs, t = struct.unpack_from("<IH", data, off)
        out["wheel_revs"] = revs
        out["wheel_event_time"] = t
        off += 6

    if flags & 0x20 and off + 4 <= len(data):  # Crank revs
        revs, t = struct.unpack_from("<HH", data, off)
        out["crank_revs"] = revs
        out["crank_event_time"] = t
        off += 4

    if flags & 0x40 and off + 4 <= len(data):
        out["extreme_force_max"], out["extreme_force_min"] = struct.unpack_from("<hh", data, off)
        off += 4
    if flags & 0x80 and off + 4 <= len(data):
        out["extreme_torque_max"], out["extreme_torque_min"] = struct.unpack_from("<hh", data, off)
        off += 4
    if flags & 0x100 and off + 2 <= len(data):
        out["extreme_angles"] = struct.unpack_from("<H", data, off)[0]
        off += 2
    if flags & 0x200 and off + 2 <= len(data):
        out["top_dead_spot"] = struct.unpack_from("<H", data, off)[0]
        off += 2
    if flags & 0x400 and off + 2 <= len(data):
        out["bottom_dead_spot"] = struct.unpack_from("<H", data, off)[0]
        off += 2
    if flags & 0x800 and off + 2 <= len(data):
        energy = struct.unpack_from("<H", data, off)[0]
        out["accumulated_energy_kj"] = energy
        out["event_count"] = energy & 0xFF
        off += 2

    # Alcuni firmware Wattbike lasciano 3 byte residuali (non standard)
    if off + 3 <= len(data) and "crank_revs" not in out:
        # prova crank uint16 + padding
        maybe_revs = struct.unpack_from("<H", data, off)[0]
        if maybe_revs or data[off : off + 3] != b"\x00\x00\x00":
            out["crank_revs_guess"] = maybe_revs

    return out


def enrich_kinematics(decoded: dict[str, Any], cadence: float | None) -> dict[str, Any]:
    """Riempie cadenza / ω / coppia come sulle righe ANT+."""
    if cadence is not None:
        cad_i = int(round(cadence))
        decoded["cadence_rpm"] = cad_i
        if cad_i > 0:
            omega = cad_i * (2.0 * math.pi / 60.0)
            decoded["angular_velocity_rad_s"] = round(omega, 4)
            pwr = decoded.get("instantaneous_power_w")
            if pwr is not None and omega > 0:
                decoded["torque_nm"] = round(float(pwr) / omega, 3)
        else:
            decoded["angular_velocity_rad_s"] = 0.0
            decoded["torque_nm"] = 0.0 if decoded.get("instantaneous_power_w") == 0 else decoded.get(
                "torque_nm"
            )
    pwr = decoded.get("instantaneous_power_w")
    if pwr == 0:
        decoded["cadence_rpm"] = decoded.get("cadence_rpm") if decoded.get("cadence_rpm") is not None else 0
        if decoded.get("cadence_rpm") == 0:
            decoded["angular_velocity_rad_s"] = 0.0
            decoded["torque_nm"] = 0.0
        if decoded.get("left_power_w") is None:
            decoded["left_power_w"] = 0
        if decoded.get("right_power_w") is None:
            decoded["right_power_w"] = 0
    return decoded


def make_ble_row(
    *,
    device_id: int,
    raw: bytes,
    started_at: float,
    decoded: dict[str, Any],
    source_detail: str = "ble_cps",
    event_count: int | None = None,
) -> dict[str, Any]:
    now = time.time()
    return {
        "timestamp_iso": datetime.fromtimestamp(now).isoformat(timespec="milliseconds"),
        "elapsed_s": round(now - started_at, 3),
        "device_id": device_id,
        "device_type": 11,  # stesso device type PowerMeter ANT+
        "device_type_name": "PowerMeter",
        "page": decoded.get("page", "ble_cps"),
        "page_name": decoded.get("page_name", "ble_cycling_power"),
        "event_count": event_count if event_count is not None else decoded.get("event_count"),
        "instantaneous_power_w": decoded.get("instantaneous_power_w"),
        "average_power_w": decoded.get("average_power_w"),
        "cadence_rpm": decoded.get("cadence_rpm"),
        "left_power_w": decoded.get("left_power_w"),
        "right_power_w": decoded.get("right_power_w"),
        "torque_nm": decoded.get("torque_nm"),
        "angular_velocity_rad_s": decoded.get("angular_velocity_rad_s"),
        "raw_bytes_hex": _bytes_to_hex(raw),
        "source": "ble",
        "source_detail": source_detail,
    }


class BleWattbikeSession:
    """
    Connessione BLE in thread dedicato (asyncio).
    Compatibile con LiveSession ANT+: stesso on_row / capturing.
    Deduplica notifiche CPS identiche (la Wattbike rinotifica lo stesso payload).
    """

    def __init__(
        self,
        address: str | None = None,
        name_substr: str = "Wattbike",
        device_id: int = 0,
        on_row: RowFn | None = None,
        on_status: StatusFn | None = None,
        include_proprietary: bool = False,
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
        self._last_cadence: float | None = None
        self._last_raw: bytes | None = None
        self._last_emit_mono: float = 0.0
        self._event_seq = 0
        self._lock = threading.Lock()
        # Zeri riammessi a ≤4 Hz se il firmware rinotifica lo stesso payload a riposo
        self._zero_reemit_s = 0.25

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
        self._ready.wait(timeout=25.0)
        if self._error and not self.connected:
            raise RuntimeError(self._error)

    def begin_capture(self) -> None:
        with self._lock:
            self._started_at = time.time()
            self._crank_prev = None
            self._last_raw = None
            self._last_emit_mono = 0.0
            self._last_cadence = None
            self._event_seq = 0
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

        def on_csc(_handle: int, data: bytearray) -> None:
            parsed = parse_csc_measurement(bytes(data))
            if "crank_revs" not in parsed or "crank_event_time" not in parsed:
                return
            cad, self._crank_prev = cadence_from_crank(
                self._crank_prev,
                int(parsed["crank_revs"]),
                int(parsed["crank_event_time"]),
            )
            if cad is not None:
                self._last_cadence = cad

        def on_cpm(_handle: int, data: bytearray) -> None:
            raw = bytes(data)
            now = time.monotonic()
            decoded = parse_cycling_power_measurement(raw)
            if not decoded:
                return
            power = int(decoded.get("instantaneous_power_w") or 0)

            # Dedup payload identici (spam Wattbike):
            # - raw diverso → emetti (max frequenza)
            # - raw uguale e W>0 → scarta
            # - raw uguale e W==0 → riammetti ogni ~0.25s (zeri a riposo)
            if self._last_raw is not None and raw == self._last_raw:
                if power > 0:
                    return
                if (now - self._last_emit_mono) < self._zero_reemit_s:
                    return

            if "crank_revs" in decoded and "crank_event_time" in decoded:
                cad, self._crank_prev = cadence_from_crank(
                    self._crank_prev,
                    int(decoded["crank_revs"]),
                    int(decoded["crank_event_time"]),
                )
                if cad is not None:
                    self._last_cadence = cad

            cadence = self._last_cadence
            if power == 0:
                cadence = 0.0
            elif cadence is None or cadence == 0:
                # CSC in ritardo: non azzerare cadenza se stai ancora spingendo
                cadence = self._last_cadence if self._last_cadence not in (None, 0) else cadence
            enrich_kinematics(decoded, cadence)

            self._event_seq = (self._event_seq + 1) & 0xFF
            ev = decoded.get("event_count")
            if ev is None:
                ev = self._event_seq

            did = self.device_id or self.resolved_device_id or 0
            self._last_raw = raw
            self._last_emit_mono = now
            emit(
                make_ble_row(
                    device_id=did,
                    raw=raw,
                    started_at=self._started_at,
                    decoded=decoded,
                    event_count=int(ev),
                )
            )

        self._status(f"Connessione BLE a {addr}...")
        async with BleakClient(addr, timeout=20.0) as client:
            self.connected = True
            self._status("Bluetooth Wattbike connesso (CPS+CSC)")
            self._ready.set()
            await client.start_notify(UUID_CPM, on_cpm)
            try:
                await client.start_notify(UUID_CSC_MEAS, on_csc)
            except Exception as exc:
                self._status(f"BLE CSC non disponibile: {exc}")
            if self.include_proprietary:
                try:
                    await client.start_notify(
                        UUID_WB_NOTIFY,
                        lambda _h, d: None,
                    )
                except Exception:
                    pass

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
