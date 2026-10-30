"""Rilevamento e installazione driver/permessi chiavetta ANT+ (plug-and-play)."""

from __future__ import annotations

import json
import os
import platform
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


def configure_usb_backend() -> None:
    """Punta pyusb alle DLL/dylib bundled o in cache (una sola volta)."""
    global _usb_backend_configured
    if _usb_backend_configured:
        return
    _usb_backend_configured = True

    candidates: list[Path] = []
    if sys.platform.startswith("win"):
        for name in ("libusb0.dll", "libusb-1.0.dll", "libusb0_x86.dll"):
            candidates += [
                _exe_dir() / name,
                _app_data_dir() / "libusb-win32" / "bin" / _windows_arch_dir() / name,
                _resource_path("resources", "windows", name),
            ]
    elif sys.platform == "darwin":
        # Frameworks (PyInstaller .app) + brew
        exe = Path(sys.executable).resolve()
        frameworks = exe.parent.parent / "Frameworks"
        candidates += [
            frameworks / "libusb-1.0.0.dylib",
            frameworks / "libusb-1.0.dylib",
            Path("/opt/homebrew/lib/libusb-1.0.dylib"),
            Path("/usr/local/lib/libusb-1.0.dylib"),
            _resource_path("resources", "macos", "libusb-1.0.dylib"),
        ]
    else:
        candidates += [
            Path("/usr/lib/x86_64-linux-gnu/libusb-1.0.so.0"),
            Path("/usr/lib/libusb-1.0.so.0"),
            Path("/lib/x86_64-linux-gnu/libusb-1.0.so.0"),
        ]

    lib_path = next((c for c in candidates if c.is_file()), None)
    if lib_path is None:
        return

    # Aiuta find_library / ctypes
    os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", str(lib_path.parent))
    os.environ.setdefault("LD_LIBRARY_PATH", str(lib_path.parent))
    try:
        import usb.backend.libusb1 as libusb1

        def _find(_name: str) -> str:
            return str(lib_path)

        # Solo registra il backend: non chiamare usb.core.find qui (può bloccarsi)
        libusb1.get_backend(find_library=_find)
    except Exception:
        pass
    if sys.platform.startswith("win"):
        try:
            import usb.backend.libusb0 as libusb0

            def _find0(_name: str) -> str:
                return str(lib_path)

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


def discover_ant_hardware_ids() -> list[str]:
    """
    Hardware ID Windows/USB per filtri libusb: stick collegate + seed + PnP.
    Formato: USB\\VID_0FCF&PID_XXXX
    """
    ids: list[str] = []

    def _add(pid: int) -> None:
        hid = f"USB\\VID_{ANT_VID:04X}&PID_{pid:04X}"
        if hid not in ids:
            ids.append(hid)

    for pid in ANT_PIDS_SEED:
        _add(pid)
    for _vid, pid in list_ant_sticks():
        _add(pid)

    if sys.platform.startswith("win"):
        # Qualsiasi device già visto da Windows con VID_0FCF (anche non ancora libusb)
        ps = (
            "Get-PnpDevice -ErrorAction SilentlyContinue | "
            "Where-Object { $_.InstanceId -match 'VID_0FCF' } | "
            "ForEach-Object { $_.InstanceId }"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            for line in (proc.stdout or "").splitlines():
                line = line.strip().upper()
                if "VID_0FCF" not in line:
                    continue
                # USB\VID_0FCF&PID_1008\... → USB\VID_0FCF&PID_1008
                parts = line.split("\\")
                if len(parts) >= 2 and parts[0] == "USB" and "PID_" in parts[1]:
                    hid = "\\".join(parts[:2]).split("&MI_")[0]
                    if hid not in ids:
                        ids.append(hid)
                    # estrai PID numerico anche per seed futuro
                    try:
                        pid_hex = parts[1].split("PID_")[1].split("&")[0]
                        _add(int(pid_hex, 16))
                    except Exception:
                        pass
        except Exception:
            pass

    return ids


def stick_accessible(timeout_s: float = 3.0) -> bool:
    """True se openant trova un backend driver compatibile (con timeout)."""
    configure_usb_backend()
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
    present = bool(sticks)
    accessible = stick_accessible()

    if plat.startswith("win"):
        prepared = is_backend_prepared() or _windows_filter_likely_ok()
        if present and accessible:
            return DriverStatus(
                True, plat, True, True,
                "Chiavetta ANT pronta — puoi premere Start.",
                False, True,
            )
        if present and not accessible:
            return DriverStatus(
                False, plat, True, False,
                "Chiavetta rilevata ma non accessibile: installo il driver libusb…",
                True, prepared,
            )
        # Senza stick: find_driver può fallire anche con filtri OK
        if prepared:
            return DriverStatus(
                True, plat, False, False,
                "Driver ANT pronti. Collega la chiavetta USB ANT+.",
                False, True,
            )
        return DriverStatus(
            False, plat, False, False,
            "Configurazione driver ANT+ necessaria (una sola volta, richiede admin).",
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
    # Backend già ok (anche senza stick) → non rilanciare brew/UAC
    if st.backend_ready and not st.can_auto_install:
        return st
    if st.stick_accessible:
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


def _ensure_windows(on_status: StatusFn | None) -> DriverStatus:
    bin_dir = _ensure_libusb_win32_files(on_status)
    if bin_dir is None:
        return diagnose()

    filter_exe = bin_dir / "install-filter.exe"
    hw_ids = discover_ant_hardware_ids()
    _status(
        on_status,
        f"Installazione driver ANT+ per {len(hw_ids)} hardware ID (UAC se richiesto)…",
    )
    # Uno script elevato unico: seed + stick collegate + PnP Windows (universale)
    lines = ["@echo off", "setlocal"]
    for device_id in hw_ids:
        lines.append(f'"{filter_exe}" install --device={device_id}')
    lines.append("exit /b 0")
    bat = _app_data_dir() / "install_ant_filters.bat"
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    ps = (
        f'Start-Process -FilePath "{bat}" -Verb RunAs -Wait -WindowStyle Hidden'
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            check=False,
            timeout=180,
        )
    except Exception as exc:
        _status(on_status, f"Errore elevazione UAC: {exc}")
        return diagnose()

    mark_backend_prepared("libusb-win32 filters installed")
    _status(on_status, "Driver installati. Se la chiavetta era collegata, ristaccala una volta.")
    return diagnose()


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
