"""Rilevamento e installazione driver/permessi chiavetta ANT+ (plug-and-play)."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Dynastream / Garmin / Suunto ANT USB sticks — vendor unico, PID dinamici
ANT_VID = 0x0FCF
# Seed noti (Garmin stick, USB2, USB-m, …). In più: qualunque PID rilevato a runtime.
ANT_PIDS_SEED = (
    0x1003, 0x1004, 0x1005, 0x1006, 0x1008, 0x1009,
    0x100A, 0x100B, 0x100C, 0x100D, 0x100E, 0x100F,
)

LIBUSB_WIN32_ZIP = (
    "https://github.com/mcuee/libusb-win32/releases/download/"
    "release_1.4.0.2/libusb-win32-bin-1.4.0.2.zip"
)
ZADIG_URL = "https://github.com/pbatard/libwdi/releases/download/v1.5.1/zadig-2.9.exe"

StatusFn = Callable[[str], None]
_PREPARED_FLAG = "drivers_prepared.json"
_usb_backend_configured = False
# Windows: nasconde qualsiasi console di powershell/cmd
_CREATE_NO_WINDOW = 0x08000000


def _win_run(
    args: list[str],
    *,
    timeout: float | None = 60,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Esegue un comando Windows senza mostrare finestre Terminale."""
    kwargs: dict = {
        "args": args,
        "check": False,
        "timeout": timeout,
        "text": True,
    }
    if capture:
        kwargs["capture_output"] = True
    else:
        kwargs["stdin"] = subprocess.DEVNULL
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    return subprocess.run(**kwargs)

@dataclass
class DriverStatus:
    ok: bool
    platform: str
    stick_present: bool
    stick_accessible: bool
    detail: str
    can_auto_install: bool
    backend_ready: bool = False

    @property
    def accessible(self) -> bool:
        """Alias compatibile con la GUI."""
        return self.stick_accessible


def _status(cb: StatusFn | None, msg: str) -> None:
    if cb:
        cb(msg)


def _app_data_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    path = base / "WattbikeLogger"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resource_path(*parts: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _prepared_path() -> Path:
    return _app_data_dir() / _PREPARED_FLAG


def is_backend_prepared() -> bool:
    p = _prepared_path()
    if not p.is_file():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return bool(data.get("ok")) and data.get("platform") == sys.platform
    except Exception:
        return False


def mark_backend_prepared(detail: str = "") -> None:
    payload = {
        "ok": True,
        "platform": sys.platform,
        "ts": time.time(),
        "detail": detail,
        "version": 2,
    }
    _prepared_path().write_text(json.dumps(payload), encoding="utf-8")


def clear_backend_prepared() -> None:
    try:
        _prepared_path().unlink(missing_ok=True)
    except OSError:
        pass


def _win_dll_candidates(name: str) -> list[Path]:
    arch = _windows_arch_dir()
    return [
        _exe_dir() / name,
        _app_data_dir() / "libusb-win32" / "bin" / arch / name,
        _app_data_dir() / "driver_setup" / name,
        _resource_path("resources", "windows", name),
    ]


def configure_usb_backend() -> None:
    """Punta pyusb alle DLL/dylib bundled o in cache (una sola volta)."""
    global _usb_backend_configured
    if _usb_backend_configured:
        return
    _usb_backend_configured = True

    libusb1_path: Path | None = None
    libusb0_path: Path | None = None

    if sys.platform.startswith("win"):
        libusb0_path = next(
            (c for n in ("libusb0.dll", "libusb0_x86.dll") for c in _win_dll_candidates(n) if c.is_file()),
            None,
        )
        libusb1_path = next(
            (c for c in _win_dll_candidates("libusb-1.0.dll") if c.is_file()),
            None,
        )
    elif sys.platform == "darwin":
        exe = Path(sys.executable).resolve()
        frameworks = exe.parent.parent / "Frameworks"
        for c in (
            frameworks / "libusb-1.0.0.dylib",
            frameworks / "libusb-1.0.dylib",
            Path("/opt/homebrew/lib/libusb-1.0.dylib"),
            Path("/usr/local/lib/libusb-1.0.dylib"),
            _resource_path("resources", "macos", "libusb-1.0.dylib"),
        ):
            if c.is_file():
                libusb1_path = c
                break
    else:
        for c in (
            Path("/usr/lib/x86_64-linux-gnu/libusb-1.0.so.0"),
            Path("/usr/lib/libusb-1.0.so.0"),
            Path("/lib/x86_64-linux-gnu/libusb-1.0.so.0"),
        ):
            if c.is_file():
                libusb1_path = c
                break

    for p in (libusb1_path, libusb0_path):
        if p is not None:
            os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", str(p.parent))
            os.environ.setdefault("LD_LIBRARY_PATH", str(p.parent))
            break

    if libusb1_path is not None:
        try:
            import usb.backend.libusb1 as libusb1

            path = str(libusb1_path)

            def _find1(_name: str) -> str:
                return path

            libusb1.get_backend(find_library=_find1)
        except Exception:
            pass

    if libusb0_path is not None:
        try:
            import usb.backend.libusb0 as libusb0

            path0 = str(libusb0_path)

            def _find0(_name: str) -> str:
                return path0

            libusb0.get_backend(find_library=_find0)
        except Exception:
            pass


def list_ant_sticks() -> list[tuple[int, int]]:
    """Elenca (vid, pid) di QUALSIASI dispositivo USB con VID ANT (0x0FCF)."""
    configure_usb_backend()
    found: list[tuple[int, int]] = []
    try:
        import usb.core
    except ImportError:
        return found

    def _scan() -> None:
        try:
            # Universale: tutto il vendor Dynastream/Garmin ANT, qualsiasi PID
            for dev in usb.core.find(find_all=True, idVendor=ANT_VID) or []:
                pair = (int(dev.idVendor), int(dev.idProduct))
                if pair not in found:
                    found.append(pair)
        except Exception:
            pass

    t = threading.Thread(target=_scan, daemon=True, name="usb-scan")
    t.start()
    t.join(2.5)
    return list(found)


@dataclass
class PnpAntDevice:
    """Chiavetta vista da Windows PnP (anche senza driver → 'Altri dispositivi')."""

    instance_id: str
    friendly_name: str
    status: str
    problem: str
    hardware_id: str  # USB\VID_0FCF&PID_XXXX


def list_ant_pnp_devices() -> list[PnpAntDevice]:
    """
    Elenca stick ANT da Gestione dispositivi (Windows).
    Funziona anche se il device è in 'Altri dispositivi' senza driver.
    """
    if not sys.platform.startswith("win"):
        return []
    # JSON via PowerShell — nessuna finestra
    ps = r"""
$ErrorActionPreference='SilentlyContinue'
$devs = Get-PnpDevice | Where-Object {
  $_.InstanceId -match 'VID_0FCF' -or $_.FriendlyName -match 'ANT\s*USB'
}
$out = @()
foreach ($d in $devs) {
  $hid = ($d.InstanceId -split '\\')[0..1] -join '\'
  if ($hid -match '&MI_') { $hid = ($hid -split '&MI_')[0] }
  $out += [PSCustomObject]@{
    InstanceId = $d.InstanceId
    FriendlyName = $d.FriendlyName
    Status = [string]$d.Status
    Problem = [string]$d.Problem
    HardwareId = $hid.ToUpper()
  }
}
$out | ConvertTo-Json -Compress
"""
    try:
        proc = _win_run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden",
             "-ExecutionPolicy", "Bypass", "-Command", ps],
            timeout=25,
        )
        raw = (proc.stdout or "").strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        result: list[PnpAntDevice] = []
        for item in data:
            result.append(
                PnpAntDevice(
                    instance_id=str(item.get("InstanceId") or ""),
                    friendly_name=str(item.get("FriendlyName") or "ANT USB"),
                    status=str(item.get("Status") or ""),
                    problem=str(item.get("Problem") or ""),
                    hardware_id=str(item.get("HardwareId") or "").upper(),
                )
            )
        return result
    except Exception:
        return []


def discover_ant_hardware_ids() -> list[str]:
    """
    Hardware ID Windows/USB per installazione driver: seed + pyusb + PnP.
    Formato: USB\\VID_0FCF&PID_XXXX
    """
    ids: list[str] = []

    def _add(pid: int) -> None:
        hid = f"USB\\VID_{ANT_VID:04X}&PID_{pid:04X}"
        if hid not in ids:
            ids.append(hid)

    def _add_hid(hid: str) -> None:
        hid = hid.upper().split("&MI_")[0]
        if hid.startswith("USB\\") and hid not in ids:
            ids.append(hid)

    for pid in ANT_PIDS_SEED:
        _add(pid)
    for _vid, pid in list_ant_sticks():
        _add(pid)
    for dev in list_ant_pnp_devices():
        if "VID_0FCF" in dev.hardware_id:
            _add_hid(dev.hardware_id)
        elif "VID_0FCF" in dev.instance_id.upper():
            parts = dev.instance_id.upper().split("\\")
            if len(parts) >= 2:
                _add_hid("\\".join(parts[:2]))

    return ids


def windows_stick_present() -> tuple[bool, str]:
    """
    True se Windows vede una chiavetta ANT (anche senza driver).
    Restituisce (present, dettaglio amichevole).
    """
    pnp = list_ant_pnp_devices()
    if pnp:
        names = ", ".join(sorted({d.friendly_name for d in pnp if d.friendly_name}))
        bad = [d for d in pnp if d.status.upper() not in ("OK", "") or d.problem not in ("", "0", "CM_PROB_NONE")]
        # Status Error / Unknown → tipicamente 'Altri dispositivi'
        if any(d.status.upper() in ("ERROR", "UNKNOWN", "DEGRADED") for d in pnp) or bad:
            return True, (
                f"Rilevata «{names or 'ANT USB'}» ma senza driver corretto "
                f"(Gestione dispositivi → Altri dispositivi). Installo il driver…"
            )
        return True, f"Chiavetta USB rilevata: {names or 'ANT'}"
    # Fallback pyusb
    sticks = list_ant_sticks()
    if sticks:
        pids = ", ".join(f"{pid:04X}" for _, pid in sticks)
        return True, f"Chiavetta ANT USB (PID {pids})"
    return False, ""


def _windows_pnp_function_driver_ok() -> bool | None:
    """
    None = nessuna stick PnP (non usare come veto).
    True = almeno una stick con Status OK e senza problem code.
    False = stick vista ma senza driver di funzione (Error/Unknown/FAILED_INSTALL).
    """
    pnp = list_ant_pnp_devices()
    if not pnp:
        return None
    for d in pnp:
        problem = (d.problem or "").upper()
        status = (d.status or "").upper()
        if status == "OK" and problem in ("", "0", "CM_PROB_NONE"):
            return True
    return False


def stick_accessible(timeout_s: float = 3.0) -> bool:
    """True se la chiavetta è davvero usabile (driver di funzione + backend libusb)."""
    configure_usb_backend()
    # find_driver() dice solo che libusb è caricabile — NON che la stick ha un driver.
    # Su Windows senza WinUSB/libusb0 la stick resta in Altri dispositivi / FAILED_INSTALL
    # e openant poi si blocca su scan/start.
    if sys.platform.startswith("win"):
        pnp_ok = _windows_pnp_function_driver_ok()
        if pnp_ok is False:
            return False

    result = [False]

    def _run() -> None:
        try:
            from openant.base.driver import find_driver

            find_driver()
            result[0] = True
        except Exception:
            result[0] = False

    t = threading.Thread(target=_run, daemon=True, name="ant-find-driver")
    t.start()
    t.join(timeout_s)
    return bool(result[0])


def _linux_udev_installed() -> bool:
    return Path("/etc/udev/rules.d/42-ant-usb-sticks.rules").is_file()


def _windows_filter_likely_ok() -> bool:
    """Heuristic: DLL presente accanto all'exe o in cache."""
    arch = _windows_arch_dir()
    dlls = [
        _exe_dir() / "libusb0.dll",
        _app_data_dir() / "libusb-win32" / "bin" / arch / "libusb0.dll",
        _resource_path("resources", "windows", "libusb0.dll"),
    ]
    return any(p.is_file() for p in dlls) and is_backend_prepared()


def _libusb_library_present() -> bool:
    """True se la shared library libusb è sul disco (anche senza stick)."""
    if sys.platform.startswith("win"):
        return _windows_filter_likely_ok() or (_exe_dir() / "libusb0.dll").is_file()
    if sys.platform == "darwin":
        exe = Path(sys.executable).resolve()
        frameworks = exe.parent.parent / "Frameworks"
        paths = [
            frameworks / "libusb-1.0.0.dylib",
            frameworks / "libusb-1.0.dylib",
            Path("/opt/homebrew/lib/libusb-1.0.dylib"),
            Path("/opt/homebrew/opt/libusb/lib/libusb-1.0.dylib"),
            Path("/usr/local/lib/libusb-1.0.dylib"),
        ]
        return any(p.is_file() for p in paths) or getattr(sys, "frozen", False)
    paths = [
        Path("/usr/lib/x86_64-linux-gnu/libusb-1.0.so.0"),
        Path("/lib/x86_64-linux-gnu/libusb-1.0.so.0"),
        Path("/usr/lib/libusb-1.0.so.0"),
    ]
    return any(p.is_file() for p in paths)


def diagnose() -> DriverStatus:
    configure_usb_backend()
    plat = sys.platform
    sticks = list_ant_sticks()
    accessible = stick_accessible()
    present = bool(sticks)

    if plat.startswith("win"):
        pnp_present, pnp_detail = windows_stick_present()
        present = pnp_present or present
        prepared = is_backend_prepared() or _windows_filter_likely_ok()
        if present and accessible:
            return DriverStatus(
                True, plat, True, True,
                "Chiavetta ANT pronta — puoi premere Start.",
                False, True,
            )
        if present and not accessible:
            detail = pnp_detail or (
                "Chiavetta rilevata ma non accessibile: installo il driver…"
            )
            return DriverStatus(
                False, plat, True, False, detail, True, prepared,
            )
        if prepared:
            return DriverStatus(
                True, plat, False, False,
                "Driver ANT pronti. Collega la chiavetta USB ANT+.",
                False, True,
            )
        return DriverStatus(
            False, plat, False, False,
            "Configurazione driver ANT+ necessaria (una sola volta, conferma admin).",
            True, False,
        )

    if plat.startswith("linux"):
        rules_ok = _linux_udev_installed()
        if accessible and present:
            return DriverStatus(
                True, plat, True, True,
                "Chiavetta ANT pronta — puoi premere Start.",
                False, True,
            )
        if accessible and not present:
            return DriverStatus(
                True, plat, False, True,
                "Permessi USB OK. Collega la chiavetta ANT+.",
                False, True,
            )
        if not rules_ok:
            return DriverStatus(
                False, plat, present, False,
                "Installo le regole udev per la chiavetta ANT (richiede password)…",
                True, False,
            )
        if present and not accessible:
            return DriverStatus(
                False, plat, True, False,
                "Chiavetta presente ma non accessibile. Ricollegala dopo le regole udev.",
                True, True,
            )
        return DriverStatus(
            True, plat, False, False,
            "Regole udev OK. Collega la chiavetta ANT+.",
            False, True,
        )

    # macOS — find_driver spesso fallisce SENZA stick: non usarlo come unico segnale
    lib_ok = _libusb_library_present()
    if present and accessible:
        return DriverStatus(
            True, plat, True, True,
            "Chiavetta ANT pronta — puoi premere Start.",
            False, True,
        )
    if present and not accessible:
        return DriverStatus(
            False, plat, True, False,
            "Chiavetta rilevata ma non apribile: riparo libusb…",
            True, lib_ok,
        )
    if lib_ok or getattr(sys, "frozen", False):
        return DriverStatus(
            True, plat, False, False,
            "Collega la chiavetta USB ANT+.",
            False, True,
        )
    return DriverStatus(
        False, plat, False, False,
        "Serve libusb (Homebrew). Configurazione automatica disponibile.",
        True, False,
    )


def ensure_drivers(on_status: StatusFn | None = None, interactive: bool = True) -> DriverStatus:
    """
    Prepara il sistema (filtro / udev / libusb) anche senza chiavetta collegata,
    poi ritenta la diagnosi. Idempotente.
    """
    _status(on_status, "Preparazione supporto ANT+…")
    if sys.platform.startswith("win"):
        _ensure_windows(on_status)
    elif sys.platform.startswith("linux"):
        _ensure_linux(on_status)
    elif sys.platform == "darwin":
        _ensure_macos(on_status)

    # Lascia tempo al kernel di riprendere la stick dopo il filtro
    time.sleep(0.8)
    st = diagnose()
    _status(on_status, st.detail)
    return st


def prepare_plug_and_play(on_status: StatusFn | None = None) -> DriverStatus:
    """
    Entry point all'avvio GUI: prepara i driver se necessario, senza chiedere.
    Se già pronti, solo diagnostica veloce.
    """
    configure_usb_backend()
    st = diagnose()
    if st.stick_accessible:
        return st
    # Stick presente ma non usabile (es. Altri dispositivi / FAILED_INSTALL) → installa subito
    if st.stick_present and not st.stick_accessible and st.can_auto_install:
        return ensure_drivers(on_status=on_status, interactive=False)
    # Backend già ok (anche senza stick) → non rilanciare brew/UAC
    if st.backend_ready and not st.can_auto_install:
        return st
    # Windows/Linux: prepara filtri/udev anche a tavolino; macOS solo se manca libusb
    if st.can_auto_install:
        return ensure_drivers(on_status=on_status, interactive=False)
    return st


def _download(url: str, dest: Path, on_status: StatusFn | None = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _status(on_status, f"Download {dest.name}…")
    req = urllib.request.Request(url, headers={"User-Agent": "WattbikeLogger"})
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as fh:
        shutil.copyfileobj(resp, fh)
    return dest


def _windows_arch_dir() -> str:
    mach = platform.machine().lower()
    if mach in ("arm64", "aarch64"):
        return "arm64"
    if mach in ("x86", "i386", "i686"):
        return "x86"
    return "amd64"


def _ensure_libusb_win32_files(on_status: StatusFn | None) -> Path | None:
    """Restituisce la cartella bin/<arch> con install-filter.exe e DLL."""
    cache = _app_data_dir() / "libusb-win32"
    arch = _windows_arch_dir()
    bin_dir = cache / "bin" / arch
    filter_exe = bin_dir / "install-filter.exe"

    # Risorse vendored nel pacchetto
    vendored = _resource_path("resources", "windows")
    if (vendored / "install-filter.exe").is_file() and not filter_exe.is_file():
        bin_dir.mkdir(parents=True, exist_ok=True)
        for name in ("install-filter.exe", "libusb0.dll", "libusb0_x86.dll", "libusb0.sys"):
            src = vendored / name
            if src.is_file():
                shutil.copy2(src, bin_dir / name)

    if not filter_exe.is_file():
        zpath = cache / "libusb-win32-bin.zip"
        try:
            _download(LIBUSB_WIN32_ZIP, zpath, on_status)
            _status(on_status, "Estrazione libusb-win32…")
            with zipfile.ZipFile(zpath, "r") as zf:
                zf.extractall(cache)
            extracted = next(cache.glob("libusb-win32-bin-*"), None)
            if extracted and extracted.is_dir():
                for item in extracted.iterdir():
                    target = cache / item.name
                    if target.exists():
                        if target.is_dir():
                            shutil.rmtree(target)
                        else:
                            target.unlink()
                    shutil.move(str(item), str(target))
        except Exception as exc:
            _status(on_status, f"Download driver fallito: {exc}")
            return None

    # Copia DLL accanto all'exe (pyusb / openant)
    for dll_name in ("libusb0.dll", "libusb0_x86.dll"):
        src = bin_dir / dll_name
        if not src.is_file() and dll_name == "libusb0.dll":
            alt = bin_dir / ("libusb0.dll" if arch != "x86" else "libusb0_x86.dll")
            src = alt if alt.is_file() else src
        if src.is_file():
            try:
                shutil.copy2(src, _exe_dir() / "libusb0.dll")
            except Exception:
                pass
    return bin_dir if filter_exe.is_file() else None


WDI_SIMPLE_URLS = (
    # Preferisci asset della stessa release; fallback CI/mirror aggiornati in release notes
    "https://github.com/Gianlu1107/wattbike-ant-excel/releases/latest/download/wdi-simple.exe",
)


def _parse_pid_from_hid(hid: str) -> int | None:
    m = re.search(r"PID_([0-9A-Fa-f]{4})", hid)
    if not m:
        return None
    try:
        return int(m.group(1), 16)
    except ValueError:
        return None


def plugged_ant_targets() -> list[tuple[int, str]]:
    """(pid, friendly_name) delle stick ANT realmente collegate (PnP)."""
    out: list[tuple[int, str]] = []
    seen: set[int] = set()
    for dev in list_ant_pnp_devices():
        pid = _parse_pid_from_hid(dev.hardware_id) or _parse_pid_from_hid(dev.instance_id)
        if pid is None or pid in seen:
            continue
        seen.add(pid)
        out.append((pid, dev.friendly_name or f"ANT USB ({pid:04X})"))
    for _vid, pid in list_ant_sticks():
        if pid not in seen:
            seen.add(pid)
            out.append((pid, f"ANT USB ({pid:04X})"))
    if not out:
        # Seed più comuni se PnP non ha ancora esposto il PID
        for pid in (0x1008, 0x1009, 0x1004, 0x1003):
            out.append((pid, f"ANT USB ({pid:04X})"))
    return out


def _find_wdi_simple() -> Path | None:
    candidates = [
        _exe_dir() / "wdi-simple.exe",
        _app_data_dir() / "driver_setup" / "wdi-simple.exe",
        _resource_path("resources", "windows", "wdi-simple.exe"),
    ]
    return next((p for p in candidates if p.is_file()), None)


def _ensure_wdi_simple(on_status: StatusFn | None) -> Path | None:
    found = _find_wdi_simple()
    if found is not None:
        return found
    dest = _app_data_dir() / "driver_setup" / "wdi-simple.exe"
    dest.parent.mkdir(parents=True, exist_ok=True)
    for url in WDI_SIMPLE_URLS:
        try:
            _status(on_status, "Download installer driver (libwdi)…")
            _download(url, dest, on_status)
            if dest.is_file() and dest.stat().st_size > 50_000:
                return dest
        except Exception as exc:
            _status(on_status, f"Download wdi-simple fallito: {exc}")
    return None


def _ensure_libusb1_dll(on_status: StatusFn | None) -> Path | None:
    """DLL libusb-1.0 per backend WinUSB (dopo wdi-simple -t 0)."""
    for c in _win_dll_candidates("libusb-1.0.dll"):
        if c.is_file():
            return c
    cache = _app_data_dir() / "libusb1"
    cache.mkdir(parents=True, exist_ok=True)
    dll_dest = cache / "libusb-1.0.dll"
    if dll_dest.is_file():
        try:
            shutil.copy2(dll_dest, _exe_dir() / "libusb-1.0.dll")
        except Exception:
            pass
        return dll_dest
    # Zip ufficiale libusb (contiene MS64/dll/libusb-1.0.dll)
    zpath = cache / "libusb-binaries.zip"
    try:
        # Preferisci zip se disponibile; altrimenti skip (WinUSB userà DLL di sistema se presente)
        url = (
            "https://github.com/libusb/libusb/releases/download/v1.0.27/"
            "libusb-1.0.27-binaries.7z"
        )
        _status(on_status, "Download libusb-1.0.dll…")
        # 7z non sempre disponibile: prova anche mirror zip non ufficiale non usato;
        # se fallisce, wdi-simple WinUSB può comunque funzionare con DLL bundled dopo.
        _download(
            "https://github.com/libusb/libusb/releases/download/v1.0.26/"
            "libusb-1.0.26-binaries.7z",
            zpath,
            on_status,
        )
    except Exception:
        return None
    # Estrazione 7z richiede 7z.exe — se non c'è, ignora
    seven = shutil.which("7z") or shutil.which("7za")
    if not seven:
        return None
    try:
        subprocess.run(
            [seven, "x", str(zpath), f"-o{cache}", "-y"],
            check=False,
            timeout=120,
            capture_output=True,
        )
        found = next(cache.rglob("libusb-1.0.dll"), None)
        if found and found.is_file():
            shutil.copy2(found, dll_dest)
            try:
                shutil.copy2(dll_dest, _exe_dir() / "libusb-1.0.dll")
            except Exception:
                pass
            return dll_dest
    except Exception:
        pass
    return None


def _ensure_windows(on_status: StatusFn | None) -> DriverStatus:
    """
    Installa driver SENZA Terminale.
    pnputil con INF non firmati fallisce (0xE000022F) su Win10/11:
    usiamo wdi-simple (libwdi, come Zadig) per WinUSB/libusb0 sul device collegato.
    """
    bin_dir = _ensure_libusb_win32_files(on_status)
    wdi = _ensure_wdi_simple(on_status)
    _ensure_libusb1_dll(on_status)

    work = _app_data_dir() / "driver_setup"
    work.mkdir(parents=True, exist_ok=True)
    log_path = work / "install.log"
    done_path = work / "install.done"
    ps1_path = work / "install_silent.ps1"
    vbs_path = work / "elevate.vbs"

    if bin_dir is not None:
        for name in ("libusb0.dll", "libusb0.sys"):
            src = bin_dir / name
            if src.is_file():
                try:
                    shutil.copy2(src, _exe_dir() / name)
                except Exception:
                    pass

    targets = plugged_ant_targets()
    filter_exe = (bin_dir / "install-filter.exe") if bin_dir else None

    try:
        log_path.write_text("", encoding="utf-8")
        done_path.unlink(missing_ok=True)
    except OSError:
        pass

    wdi_lines: list[str] = []
    if wdi is not None:
        wdi_ps = str(wdi).replace("'", "''")
        # libwdi estrae in ./usb_driver: se esiste un FILE omoimo → Access denied (-3/-11).
        # Usa sempre una cartella dedicata sotto AppData.
        extract_dir = work / "usb_driver_out"
        extract_dir.mkdir(parents=True, exist_ok=True)
        extract_ps = str(extract_dir).replace("'", "''")
        wdi_lines.append(f"$wdi = '{wdi_ps}'")
        wdi_lines.append(f"$wdiDest = '{extract_ps}'")
        wdi_lines.append("if (Test-Path $wdiDest) { Remove-Item -LiteralPath $wdiDest -Recurse -Force -ErrorAction SilentlyContinue }")
        wdi_lines.append("New-Item -ItemType Directory -Force -Path $wdiDest | Out-Null")
        wdi_lines.append("Set-Location -LiteralPath (Split-Path $wdiDest -Parent)")
        wdi_lines.append("L 'Installazione driver di funzione via libwdi (come Zadig)…'")
        # Nome senza spazi (schtasks/cmdline); PID reale dalla stick collegata
        items = ", ".join(
            "@{{Pid='0x{pid:04X}'; Name='ANTUSB'}}".format(pid=pid)
            for pid, _name in targets
        )
        wdi_lines += [
            f"$targets = @({items})",
            "foreach ($t in $targets) {",
            "  $ok = $false",
            "  foreach ($dtype in @(0,1)) {",
            "    $label = if ($dtype -eq 0) { 'WinUSB' } else { 'libusb-win32' }",
            "    L ('  ' + $t.Name + ' PID=' + $t.Pid + ' → ' + $label + '…')",
            "    if (Test-Path $wdiDest) { Remove-Item -LiteralPath $wdiDest -Recurse -Force -ErrorAction SilentlyContinue }",
            "    New-Item -ItemType Directory -Force -Path $wdiDest | Out-Null",
            "    $p = Start-Process -FilePath $wdi -ArgumentList @(",
            "      '--name', $t.Name, '--vid', '0x0FCF', '--pid', $t.Pid,",
            "      '--type', \"$dtype\", '--dest', $wdiDest, '--silent', '--timeout', '180000'",
            "    ) -Wait -PassThru -WindowStyle Hidden",
            "    L ('    exit=' + $p.ExitCode)",
            "    if ($p.ExitCode -eq 0) { $ok = $true; break }",
            "  }",
            "  if (-not $ok) { L ('  FALLITO per ' + $t.Name) }",
            "}",
        ]
    else:
        wdi_lines.append(
            "L 'wdi-simple.exe assente: i filtri soli NON rimuovono Altri dispositivi.'"
        )

    filter_lines: list[str] = []
    if filter_exe is not None and filter_exe.is_file():
        fpath = str(filter_exe).replace("'", "''")
        filter_lines.append(f"$filter = '{fpath}'")
        filter_lines.append("L 'Applico filtri libusb-win32 (seed)…'")
        for hid in discover_ant_hardware_ids()[:16]:
            hid_ps = hid.replace("'", "''")
            filter_lines += [
                f"L '  filter {hid_ps}'",
                (
                    f"$p = Start-Process -FilePath $filter -ArgumentList @('install',"
                    f"'--device={hid_ps}') -Wait -PassThru -WindowStyle Hidden"
                ),
                "L ('    exit=' + $p.ExitCode)",
            ]

    ps1_path.write_text(
        "\n".join(
            [
                "$ErrorActionPreference = 'Continue'",
                f"$log = '{log_path}'",
                f"$done = '{done_path}'",
                "function L($m) { $line = (Get-Date -Format 'HH:mm:ss') + ' ' + $m; Add-Content -Path $log -Value $line -Encoding UTF8 }",
                "L 'Avvio installazione driver ANT+ (silenziosa)…'",
                f"L ('Stick/target rilevati: {len(targets)}')",
                *wdi_lines,
                *filter_lines,
                "L 'Reset solo dispositivi VID_0FCF…'",
                "Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -match 'VID_0FCF' } | ForEach-Object {",
                "  L ('  reset ' + $_.FriendlyName + ' [' + $_.Status + '] ' + $_.InstanceId)",
                "  Disable-PnpDevice -InstanceId $_.InstanceId -Confirm:$false -ErrorAction SilentlyContinue",
                "  Start-Sleep -Milliseconds 600",
                "  Enable-PnpDevice -InstanceId $_.InstanceId -Confirm:$false -ErrorAction SilentlyContinue",
                "}",
                "L 'Installazione completata.'",
                "L 'Se resta in Altri dispositivi: scollega e ricollega la chiavetta.'",
                "Set-Content -Path $done -Value 'ok' -Encoding UTF8",
                "",
            ]
        ),
        encoding="utf-8",
    )

    vbs_path.write_text(
        "Set sh = CreateObject(\"Shell.Application\")\r\n"
        f"sh.ShellExecute \"powershell.exe\", \"-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"\"{ps1_path}\"\"\", \"\", \"runas\", 0\r\n",
        encoding="utf-8",
    )

    if wdi is None and (filter_exe is None or not filter_exe.is_file()):
        _status(on_status, "Nessun installer driver disponibile.")
        return diagnose()

    _status(on_status, "Conferma UAC admin (nessun Terminale)…")
    try:
        _win_run(["wscript.exe", "//B", "//Nologo", str(vbs_path)], timeout=30, capture=False)
    except Exception as exc:
        _status(on_status, f"Impossibile avviare installazione elevata: {exc}")
        return diagnose()

    deadline = time.time() + 200
    seen = 0
    while time.time() < deadline:
        try:
            if log_path.is_file():
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                while seen < len(lines):
                    _status(on_status, lines[seen])
                    seen += 1
            if done_path.is_file():
                break
        except OSError:
            pass
        time.sleep(0.35)

    global _usb_backend_configured
    _usb_backend_configured = False
    mark_backend_prepared("libwdi wdi-simple")
    time.sleep(1.5)
    st = diagnose()
    if st.stick_present and not st.accessible:
        _status(
            on_status,
            "Driver applicati. Scollega e ricollega la chiavetta ANT, poi attendi 2 s.",
        )
    elif st.accessible:
        _status(on_status, "Chiavetta ANT pronta — puoi premere Start.")
    return st


def _udev_rules_text() -> str:
    # Universale: tutto VID 0fcf (qualsiasi prodotto ANT), non solo PID fissi
    return """# Wattbike Logger — permessi chiavette ANT+ (qualsiasi PID, VID 0fcf)
ACTION!="add", GOTO="wattbike_ant_rules_end"
SUBSYSTEM!="usb", GOTO="wattbike_ant_rules_end"
ATTR{idVendor}=="0fcf", ENV{ID_ANT_DEVICE}="1", TAG+="uaccess", GROUP="plugdev", MODE="0666"
LABEL="wattbike_ant_rules_end"
"""


def _ensure_linux(on_status: StatusFn | None) -> DriverStatus:
    rules_src = _resource_path("resources", "42-ant-usb-sticks.rules")
    if not rules_src.is_file():
        rules_src = _app_data_dir() / "42-ant-usb-sticks.rules"
        rules_src.write_text(_udev_rules_text(), encoding="utf-8")
    else:
        # Aggiorna sempre il testo embedded in appdata per lo script
        cached = _app_data_dir() / "42-ant-usb-sticks.rules"
        try:
            cached.write_text(rules_src.read_text(encoding="utf-8"), encoding="utf-8")
            rules_src = cached
        except OSError:
            pass

    dest = "/etc/udev/rules.d/42-ant-usb-sticks.rules"
    if Path(dest).is_file() and is_backend_prepared():
        return diagnose()

    script = f"""#!/bin/bash
set -e
cp "{rules_src}" "{dest}"
chmod 644 "{dest}"
# gruppo plugdev se esiste
getent group plugdev >/dev/null && usermod -aG plugdev "$(logname 2>/dev/null || echo "$SUDO_USER")" 2>/dev/null || true
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --attr-match=idVendor=0fcf --action=add || true
"""
    tmp = Path(tempfile.mkdtemp()) / "install_ant_udev.sh"
    tmp.write_text(script, encoding="utf-8")
    tmp.chmod(0o755)
    _status(on_status, "Installazione regole udev (password admin)…")

    rc = subprocess.run(["pkexec", str(tmp)], check=False)
    if rc.returncode != 0:
        rc = subprocess.run(["sudo", "-n", str(tmp)], check=False)
    if rc.returncode != 0:
        rc = subprocess.run(["sudo", str(tmp)], check=False)
    if rc.returncode != 0:
        _status(on_status, "Installazione udev annullata o fallita.")
        return diagnose()

    mark_backend_prepared("udev rules installed")
    _status(on_status, "Regole udev installate. Ricollega la chiavetta se era inserita.")
    return diagnose()


def _ensure_macos(on_status: StatusFn | None) -> DriverStatus:
    _status(on_status, "Verifica libusb su macOS…")
    configure_usb_backend()
    if stick_accessible():
        mark_backend_prepared("libusb ok")
        return diagnose()

    if getattr(sys, "frozen", False):
        # Nell'app bundled libusb dovrebbe esserci; riprova dopo configure
        time.sleep(0.3)
        configure_usb_backend()
        if stick_accessible():
            mark_backend_prepared("libusb bundled")
            return diagnose()
        _status(
            on_status,
            "libusb non trovato nel bundle. Installa con: brew install libusb",
        )
        return diagnose()

    brew = shutil.which("brew")
    if brew:
        _status(on_status, "Installazione libusb via Homebrew…")
        subprocess.run([brew, "install", "libusb"], check=False)
        configure_usb_backend()
        if stick_accessible() or True:
            mark_backend_prepared("brew libusb")
        return diagnose()

    _status(on_status, "Installa Homebrew e poi: brew install libusb")
    return diagnose()


def open_zadig_fallback(on_status: StatusFn | None = None) -> None:
    """Scarica e avvia Zadig come piano B su Windows."""
    if not sys.platform.startswith("win"):
        return
    zadig = _app_data_dir() / "zadig-2.9.exe"
    if not zadig.is_file():
        try:
            _download(ZADIG_URL, zadig, on_status)
        except Exception as exc:
            _status(on_status, f"Download Zadig fallito: {exc}")
            return
    _status(on_status, "Apertura Zadig… Options → List All Devices → stick ANT → libusb-win32")
    subprocess.Popen([str(zadig)], close_fds=True)
