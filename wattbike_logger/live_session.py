"""Sessione ANT+ avviabile/stoppabile (per la GUI)."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from openant.devices import ANTPLUS_NETWORK_KEY
from openant.devices.common import DeviceType
from openant.devices.power_meter import PowerMeter
from openant.easy.channel import Channel
from openant.easy.node import Node

from .recorder import (
    DEFAULT_POWER_PERIOD,
    INTERESTING_DEVICE_TYPES,
    SessionRecorder,
    _decode_payload,
    _device_type_name,
    make_row,
    row_has_metrics,
)


class LiveSession:
    """
    Apre la chiavetta durante il countdown, poi abilita la cattura su 'VIA!'.
    thread-safe verso la UI tramite on_row.
    """

    def __init__(
        self,
        device_id: int = 0,
        mode: str = "scan",
        on_row: Callable[[dict[str, Any]], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_device: Callable[[int, str], None] | None = None,
        use_ble: bool = False,
        ble_address: str | None = None,
    ):
        self.device_id = device_id  # 0 = qualsiasi PowerMeter
        self.mode = mode
        self.on_row = on_row
        self.on_status = on_status
        self.on_device = on_device
        self.use_ble = use_ble
        self.ble_address = ble_address
        self.rows: list[dict[str, Any]] = []
        self.found_device_id: int | None = None
        self.capturing = False
        self._ready = False
        self._node: Node | None = None
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._seen_events: set[tuple[Any, Any, Any]] = set()
        self._started_at = time.time()
        self._use_paired = mode == "paired"
        self._meter: PowerMeter | None = None
        self._error: str | None = None
        self._ble = None
        self._last_ant_key: tuple[Any, ...] | None = None
        self._recent_emitted: list[dict[str, Any]] = []

    def _notify_device(self, device_id: int, device_type: int | None) -> None:
        if self.found_device_id is None:
            self.found_device_id = device_id
            if self.on_device:
                self.on_device(device_id, _device_type_name(device_type))

    def _status(self, msg: str) -> None:
        if self.on_status:
            self.on_status(msg)

    def _emit_row(self, row: dict[str, Any]) -> None:
        if not row_has_metrics(row):
            return

        with self._lock:
            # 1) Dedup solo ritrasmissioni ANT identiche (stesso event_count + raw).
            if row.get("source", "ant+") == "ant+":
                key = (
                    row.get("device_id"),
                    row.get("page"),
                    row.get("event_count"),
                    row.get("raw_bytes_hex"),
                )
                if key == self._last_ant_key:
                    return
                self._last_ant_key = key

            # 2) Dedup cross-source ANT+ <-> BLE e rate-limit zeri ravvicinati
            t_curr = row.get("elapsed_s")
            p_curr = row.get("instantaneous_power_w")
            c_curr = row.get("cadence_rpm")
            src_curr = row.get("source", "ant+")

            if t_curr is not None:
                for prev in reversed(self._recent_emitted):
                    t_prev = prev.get("elapsed_s")
                    if t_prev is None:
                        continue
                    dt = t_curr - t_prev
                    if dt > 0.45:
                        break
                    p_prev = prev.get("instantaneous_power_w")
                    c_prev = prev.get("cadence_rpm")
                    src_prev = prev.get("source", "ant+")

                    if src_curr != src_prev:
                        # Duplicato cross-source: stessa potenza registrata da entrambe le radio entro 0.45s
                        if p_curr is not None and p_curr > 0 and p_curr == p_prev:
                            c1 = c_curr if c_curr is not None else 0
                            c2 = c_prev if c_prev is not None else 0
                            if abs(c1 - c2) <= 2 or c1 == 0 or c2 == 0:
                                # Fondi eventuali metriche mancanti nel record precedente
                                if prev.get("torque_nm") is None and row.get("torque_nm") is not None:
                                    prev["torque_nm"] = row["torque_nm"]
                                    prev["angular_velocity_rad_s"] = row.get("angular_velocity_rad_s")
                                if (prev.get("left_power_w") is None or prev.get("right_power_w") is None) and (
                                    row.get("left_power_w") is not None and row.get("right_power_w") is not None
                                ):
                                    prev["left_power_w"] = row["left_power_w"]
                                    prev["right_power_w"] = row["right_power_w"]
                                return
                        # Zeri a riposo duplicati tra radio entro 0.22s
                        if p_curr == 0 and p_prev == 0 and dt < 0.22:
                            return
                    else:
                        # Stessa fonte: zeri duplicati troppo ravvicinati (< 0.22s)
                        if p_curr == 0 and p_prev == 0 and dt < 0.22:
                            return

            if not self.capturing:
                return

            self._recent_emitted.append(row)
            if len(self._recent_emitted) > 16:
                self._recent_emitted.pop(0)

            self.rows.append(row)

        if self.on_row:
            self.on_row(row)

    def _start_ble(self) -> None:
        if not self.use_ble:
            return
        from .ble_wattbike import BleWattbikeSession, bleak_available

        if not bleak_available():
            self._status("Bluetooth: installa bleak (pip install bleak)")
            return
        ble = BleWattbikeSession(
            address=self.ble_address,
            device_id=self.device_id,
            on_row=self._emit_row,
            on_status=self._status,
        )
        self._ble = ble
        ble.start()
        if ble.resolved_device_id and self.found_device_id is None:
            self._notify_device(ble.resolved_device_id, None)

    def open_stick(self) -> None:
        """Inizializza ANT+ (+ BLE opzionale). Blocca finché pronto o errore."""
        self._error = None
        ant_ok = False
        try:
            if self.mode == "scan":
                try:
                    self._open_rx_scan()
                    ant_ok = True
                except Exception as exc:
                    self._status(f"RX-scan fallito ({exc}), provo paired...")
                    SessionRecorder._safe_stop(self._node)
                    self._node = None
                    time.sleep(0.4)
            if not ant_ok:
                self._open_paired()
                ant_ok = True
        except Exception as exc:
            self._error = str(exc)
            SessionRecorder._safe_stop(self._node)
            self._node = None
            if not self.use_ble:
                raise
            self._status(f"ANT+ non disponibile ({exc}); provo solo Bluetooth...")

        try:
            self._start_ble()
        except Exception as exc:
            if ant_ok:
                self._status(f"Bluetooth non avviato: {exc}")
            else:
                self._error = str(exc)
                raise

        if ant_ok or (self._ble and getattr(self._ble, "connected", False)):
            self._ready = True
        elif not ant_ok:
            raise RuntimeError(self._error or "Né ANT+ né Bluetooth disponibili")

    def _open_rx_scan(self) -> None:
        self._status("Apertura chiavetta ANT+ (RX-scan)...")
        node = Node()
        self._node = node
        node.set_network_key(0x00, ANTPLUS_NETWORK_KEY)
        seen_keys: set[tuple[int, int]] = set()

        channel = node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE, 0x00, 0x01)
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
                self._status(
                    f"Segnale: device_id={device_id} ({_device_type_name(device_type)})"
                )
            self._notify_device(device_id, device_type)
            row = make_row(
                device_id=device_id,
                raw=data,
                started_at=self._started_at,
                device_type=device_type,
            )
            self._emit_row(row)

        channel.on_broadcast_data = on_data
        channel.on_burst_data = on_data
        channel.on_acknowledge = on_data
        channel.open_rx_scan_mode()
        self._use_paired = False
        self._start_node_thread(node)
        self._ready = True
        self._status("Chiavetta pronta (RX-scan)")

    def _open_paired(self) -> None:
        self._status("Apertura chiavetta ANT+ (paired)...")
        node = Node()
        self._node = node
        node.set_network_key(0x00, ANTPLUS_NETWORK_KEY)
        meter = PowerMeter(node, device_id=self.device_id)
        self._meter = meter

        def on_found() -> None:
            self._notify_device(int(meter.device_id), DeviceType.PowerMeter.value)
            self._status(f"Connesso device_id={meter.device_id}")

        original_on_data = meter.on_data

        def on_data_with_raw(data) -> None:
            original_on_data(data)
            decoded = _decode_payload(data, device_type=DeviceType.PowerMeter.value)
            if decoded["page"] == 0x10:
                avg = meter.data["power"].average_power
                if avg:
                    decoded["average_power_w"] = avg
            row = make_row(
                device_id=meter.device_id,
                raw=data,
                started_at=self._started_at,
                decoded=decoded,
                device_type=DeviceType.PowerMeter.value,
            )
            self._emit_row(row)

        meter.on_found = on_found
        meter.on_data = on_data_with_raw
        self._use_paired = True
        self._start_node_thread(node)
        self._ready = True
        self._status("Chiavetta pronta (paired)")

    def _start_node_thread(self, node: Node) -> None:
        def run() -> None:
            try:
                node.start()
            except Exception as exc:
                self._error = str(exc)

        self._worker = threading.Thread(target=run, daemon=True, name="ant-live")
        self._worker.start()

    def begin_capture(self) -> None:
        """VIA!: azzera buffer e inizia a salvare pacchetti."""
        with self._lock:
            self.rows = []
            self._seen_events.clear()
            self._last_ant_key = None
            self._recent_emitted.clear()
            self._started_at = time.time()
            self.capturing = True
        if self._ble is not None:
            self._ble.begin_capture()
        self._status("Registrazione in corso...")

    def stop(self) -> list[dict[str, Any]]:
        """Ferma ANT+/BLE e restituisce le righe catturate."""
        with self._lock:
            self.capturing = False
            rows = list(self.rows)
        self._status("Arresto...")
        if self._ble is not None:
            try:
                self._ble.stop()
            except Exception:
                pass
            self._ble = None
        SessionRecorder._safe_stop(self._node)
        self._node = None
        self._ready = False
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2.0)
        self._worker = None
        self._status("Fermato")
        return rows

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def last_error(self) -> str | None:
        return self._error
