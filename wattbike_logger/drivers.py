"""Rilevamento e installazione driver/permessi chiavetta ANT+ per OS."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ANT_VID = 0x0FCF
ANT_PIDS = (0x1008, 0x1009, 0x1004, 0x1003)

LIBUSB_WIN32_ZIP = (
    "https://github.com/mcuee/libusb-win32/releases/download/"
    "release_1.4.0.2/libusb-win32-bin-1.4.0.2.zip"
)
ZADIG_URL = "https://github.com/pbatard/libwdi/releases/download/v1.5.1/zadig-2.9.exe"

StatusFn = Callable[[str], None]


@dataclass
class DriverStatus:
    ok: bool
    platform: str
    stick_present: bool
    stick_accessible: bool
    detail: str
    can_auto_install: bool


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
    """Percorso file risorse (dev o PyInstaller)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)


def list_ant_sticks() -> list[tuple[int, int]]:
    """Elenca (vid, pid) delle chiavette ANT visibili via pyusb."""
    found: list[tuple[int, int]] = []
    try:
        import usb.core
    except ImportError:
        return found
    try:
        for pid in ANT_PIDS:
            for dev in usb.core.find(find_all=True, idVendor=ANT_VID, idProduct=pid) or []:
                found.append((int(dev.idVendor), int(dev.idProduct)))
        # catch-all other 0fcf products
        for dev in usb.core.find(find_all=True, idVendor=ANT_VID) or []:
            pair = (int(dev.idVendor), int(dev.idProduct))
            if pair not in found:
                found.append(pair)
    except Exception:
        pass
    return found


def stick_accessible() -> bool:
    """True se openant trova un backend driver compatibile."""
    try:
        from openant.base.driver import find_driver

        find_driver()
        return True
    except Exception:
        return False


def diagnose() -> DriverStatus:
    plat = sys.platform
    sticks = list_ant_sticks()
    present = bool(sticks)
    accessible = stick_accessible()

    if plat.startswith("win"):
        if accessible:
            detail = "Driver OK: chiavetta ANT accessibile."
            return DriverStatus(True, plat, present, True, detail, False)
        if present:
            detail = (
                "Chiavetta ANT rilevata ma non accessibile. "
                "Serve il filtro libusb-win32 (installazione automatica disponibile)."
            )
            return DriverStatus(False, plat, True, False, detail, True)
        detail = "Nessuna chiavetta ANT (VID 0FCF) collegata."
        return DriverStatus(False, plat, False, False, detail, False)

    if plat.startswith("linux"):
        rules = Path("/etc/udev/rules.d/42-ant-usb-sticks.rules")
        rules_ok = rules.is_file()
        if accessible:
            detail = "OK: chiavetta accessibile."
            return DriverStatus(True, plat, present, True, detail, False)
        if not rules_ok:
            detail = "Mancano le regole udev per la chiavetta ANT (permessi USB)."
            return DriverStatus(False, plat, present, False, detail, True)
        if present:
            detail = (
                "Chiavetta presente ma non accessibile. "
                "Ricollega la stick dopo le regole udev, oppure reinstalla."
            )
            return DriverStatus(False, plat, True, False, detail, True)
        detail = "Nessuna chiavetta ANT collegata."
        return DriverStatus(False, plat, False, False, detail, False)

    # macOS / altro — nessun kernel driver; serve libusb + stick collegata
    if accessible:
        detail = "OK: libusb operativo."
        return DriverStatus(True, plat, present, True, detail, False)
    if present:
        detail = (
            "Chiavetta rilevata ma libusb non riesce ad aprirla. "
            "Provo a installare/riparare libusb (Homebrew)."
        )
        return DriverStatus(False, plat, True, False, detail, True)
    detail = "Nessuna chiavetta ANT collegata."
    return DriverStatus(False, plat, False, False, detail, False)

def ensure_drivers(on_status: StatusFn | None = None, interactive: bool = True) -> DriverStatus:
    """
    Controlla e, se possibile, installa quanto serve per l'OS corrente.
    Su Windows/Linux può richiedere elevazione (UAC / polkit).
    """
    st = diagnose()
    if st.ok or st.accessible:
        _status(on_status, st.detail)
        return st

    if not st.can_auto_install:
        _status(on_status, st.detail)
        return st

    if sys.platform.startswith("win"):
        return _ensure_windows(on_status)
    if sys.platform.startswith("linux"):
        return _ensure_linux(on_status)
    if sys.platform == "darwin":
        return _ensure_macos(on_status)
    return st


def _download(url: str, dest: Path, on_status: StatusFn | None = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _status(on_status, f"Download {dest.name}...")
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


def _ensure_windows(on_status: StatusFn | None) -> DriverStatus:
    cache = _app_data_dir() / "libusb-win32"
    arch = _windows_arch_dir()
    filter_exe = cache / "bin" / arch / "install-filter.exe"
    dll_name = "libusb0.dll" if arch != "x86" else "libusb0_x86.dll"
    dll_src = cache / "bin" / arch / ("libusb0.dll" if arch != "x86" else "libusb0_x86.dll")

    if not filter_exe.is_file():
        zpath = cache / "libusb-win32-bin.zip"
        try:
            _download(LIBUSB_WIN32_ZIP, zpath, on_status)
            _status(on_status, "Estrazione libusb-win32...")
            with zipfile.ZipFile(zpath, "r") as zf:
                zf.extractall(cache)
            # zip root folder
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
            return diagnose()

    # Copia DLL accanto all'exe (utile a pyusb)
    try:
        exe_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
        if dll_src.is_file():
            shutil.copy2(dll_src, exe_dir / "libusb0.dll")
    except Exception:
        pass

    sticks = list_ant_sticks()
    pids = [pid for _, pid in sticks] or list(ANT_PIDS)
    _status(on_status, "Installazione filtro libusb-win32 (richiede admin)...")

    for pid in pids:
        device_id = f"USB\\VID_{ANT_VID:04X}&PID_{pid:04X}"
        # install-filter richiede privilegi elevati
        cmd = [
            str(filter_exe),
            "install",
            f"--device={device_id}",
        ]
        try:
            if getattr(sys, "frozen", False) or True:
                # PowerShell elevation
                ps = (
                    f'Start-Process -FilePath "{filter_exe}" '
                    f'-ArgumentList \'install "--device={device_id}"\' '
                    f"-Verb RunAs -Wait"
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                    check=False,
                )
            else:
                subprocess.run(cmd, check=False)
        except Exception as exc:
            _status(on_status, f"Errore install-filter: {exc}")

    _status(on_status, "Ricollega la chiavetta ANT se richiesto, poi riprovo…")
    return diagnose()


def _ensure_linux(on_status: StatusFn | None) -> DriverStatus:
    rules_src = _resource_path("resources", "42-ant-usb-sticks.rules")
    if not rules_src.is_file():
        # fallback embedded text
        rules_src = _app_data_dir() / "42-ant-usb-sticks.rules"
        rules_src.write_text(
            """ACTION!="add", GOTO="wattbike_ant_rules_end"
SUBSYSTEM!="usb", GOTO="wattbike_ant_rules_end"
ATTR{idVendor}=="0fcf", ATTR{idProduct}=="1008", TAG+="uaccess", GROUP="plugdev", MODE="0666"
ATTR{idVendor}=="0fcf", ATTR{idProduct}=="1009", TAG+="uaccess", GROUP="plugdev", MODE="0666"
ATTR{idVendor}=="0fcf", ATTR{idProduct}=="1004", TAG+="uaccess", GROUP="plugdev", MODE="0666"
LABEL="wattbike_ant_rules_end"
""",
            encoding="utf-8",
        )

    dest = "/etc/udev/rules.d/42-ant-usb-sticks.rules"
    script = f"""#!/bin/bash
set -e
cp "{rules_src}" "{dest}"
chmod 644 "{dest}"
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --attr-match=idVendor=0fcf --action=add
"""
    tmp = Path(tempfile.mkdtemp()) / "install_ant_udev.sh"
    tmp.write_text(script, encoding="utf-8")
    tmp.chmod(0o755)
    _status(on_status, "Installazione regole udev (richiede password admin)...")

    # pkexec (polkit) → sudo fallback
    rc = subprocess.run(["pkexec", str(tmp)], check=False)
    if rc.returncode != 0:
        rc = subprocess.run(["sudo", str(tmp)], check=False)
    if rc.returncode != 0:
        _status(on_status, "Installazione udev annullata o fallita.")
        return diagnose()

    _status(on_status, "Regole udev installate. Ricollega la chiavetta.")
    return diagnose()


def _ensure_macos(on_status: StatusFn | None) -> DriverStatus:
    """Su macOS non c'è un kernel driver: serve libusb. Prova brew se manca."""
    _status(on_status, "Verifica libusb su macOS...")
    if stick_accessible():
        return diagnose()

    brew = shutil.which("brew")
    if brew:
        _status(on_status, "Installazione libusb via Homebrew...")
        subprocess.run([brew, "install", "libusb"], check=False)
        return diagnose()

    _status(
        on_status,
        "Installa libusb: apri Terminale e esegui `brew install libusb`, "
        "oppure usa l'app buildata che include la libreria.",
    )
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
    _status(on_status, "Apertura Zadig… seleziona lo stick ANT e libusb-win32")
    subprocess.Popen([str(zadig)], close_fds=True)
