"""Controllo aggiornamenti da GitHub Releases."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__

GITHUB_REPO = "Gianlu1107/wattbike-ant-excel"
API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
USER_AGENT = f"wattbike-logger/{__version__}"


@dataclass
class ReleaseInfo:
    tag: str
    version: str
    name: str
    body: str
    asset_name: str
    asset_url: str
    asset_size: int


def _parse_version(text: str) -> tuple[int, ...]:
    text = text.strip().lstrip("vV")
    parts: list[int] = []
    for chunk in text.split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:4])


def is_newer(remote: str, local: str = __version__) -> bool:
    return _parse_version(remote) > _parse_version(local)


def _http_json(url: str, timeout: float = 12.0) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _pick_asset(assets: list[dict]) -> dict | None:
    """Sceglie l'installer nativo per la piattaforma/architettura corrente."""
    names = [(a.get("name") or "", a) for a in assets]
    mach = platform.machine().lower()

    if sys.platform.startswith("win"):
        keys = ["windows-x64", "windows", "win"]
        if mach in ("arm64", "aarch64"):
            keys = ["windows-arm64", "windows-arm", "windows"]
        preferred_exts = (".exe", ".msi", ".zip")
    elif sys.platform == "darwin":
        if mach in ("arm64", "aarch64"):
            keys = ["macos-arm64", "darwin-arm64", "macos-arm", "arm64"]
        else:
            keys = ["macos-x64", "macos-amd64", "macos-x86_64", "darwin-x64", "x86_64", "x64"]
        keys += ["macos", "darwin", "mac"]
        preferred_exts = (".pkg", ".dmg", ".zip")
    else:
        if mach in ("aarch64", "arm64"):
            keys = ["linux-arm64", "linux-aarch64", "arm64"]
        else:
            keys = ["linux-x64", "linux-amd64", "linux-x86_64", "x86_64", "x64"]
        keys += ["linux"]
        preferred_exts = (".deb", ".AppImage", ".rpm", ".zip")

    def score(name: str) -> int:
        lower = name.lower()
        s = 0
        for i, key in enumerate(keys):
            if key in lower:
                s += 100 - i
        for i, ext in enumerate(preferred_exts):
            if lower.endswith(ext):
                s += 40 - i * 5
                break
        if "setup" in lower or lower.endswith(".pkg") or lower.endswith(".msi") or lower.endswith(".deb"):
            s += 25  # installer nativo
        if "wattbike" in lower:
            s += 3
        if sys.platform != "darwin" and "macos" in lower:
            s -= 50
        if not sys.platform.startswith("win") and ("windows" in lower or (lower.endswith(".exe") and "setup" not in lower and "linux" not in lower)):
            # penalizza exe Windows su altre piattaforme; Setup.exe è comunque win-only via keys
            if not sys.platform.startswith("win"):
                s -= 50
        if not sys.platform.startswith("linux") and "linux" in lower:
            s -= 40
        return s

    ranked = sorted(((score(n), n, a) for n, a in names), key=lambda t: t[0], reverse=True)
    if ranked and ranked[0][0] > 0:
        return ranked[0][2]
    return None


def check_latest_release(
    current: str = __version__,
) -> ReleaseInfo | None:
    """Restituisce ReleaseInfo se c'è una versione più nuova, altrimenti None."""
    try:
        data = _http_json(API_LATEST)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None

    tag = data.get("tag_name") or ""
    version = tag.lstrip("vV")
    if not version or not is_newer(version, current):
        return None

    asset = _pick_asset(data.get("assets") or [])
    if not asset:
        return None

    return ReleaseInfo(
        tag=tag,
        version=version,
        name=data.get("name") or tag,
        body=(data.get("body") or "")[:2000],
        asset_name=asset.get("name") or "update.bin",
        asset_url=asset.get("browser_download_url") or "",
        asset_size=int(asset.get("size") or 0),
    )


def download_file(
    url: str,
    dest: Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with dest.open("wb") as fh:
            while True:
                chunk = resp.read(1024 * 64)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
    return dest


def frozen_executable() -> Path | None:
    """Percorso dell'eseguibile se siamo dentro PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return None


def _unpack_if_zip(path: Path, tmp_dir: Path) -> Path:
    """Se path è uno zip, lo estrae e restituisce la cartella; altrimenti path."""
    if path.suffix.lower() != ".zip":
        return path
    import zipfile

    extract_to = tmp_dir / "extracted"
    extract_to.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "r") as zf:
        zf.extractall(extract_to)
    return extract_to


def _find_in_tree(root: Path, patterns: tuple[str, ...]) -> Path | None:
    for pat in patterns:
        hits = sorted(root.rglob(pat))
        if hits:
            return hits[0]
    return None


def apply_update(
    release: ReleaseInfo,
    on_progress: Callable[[int, int], None] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> None:
    """
    Scarica l'installer della nuova versione e lo avvia (pkg / Setup.exe / deb),
    oppure sostituisce il binario se l'asset è ancora un pacchetto legacy.
    """
    exe = frozen_executable()
    if exe is None:
        raise RuntimeError("Aggiornamento automatico disponibile solo dall'eseguibile.")

    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    status(f"Download {release.asset_name}...")
    tmp_dir = Path(tempfile.mkdtemp(prefix="wattbike_update_"))
    download_path = tmp_dir / release.asset_name
    download_file(release.asset_url, download_path, on_progress=on_progress)
    lower = release.asset_name.lower()

    # Installer nativi: apri e chiudi l'app corrente
    if lower.endswith(".pkg") or lower.endswith(".dmg"):
        status("Apertura Installer macOS…")
        subprocess.Popen(["open", str(download_path)], start_new_session=True)
        sys.exit(0)
    if lower.endswith(".msi") or (lower.endswith(".exe") and "setup" in lower):
        status("Avvio Setup Windows…")
        bat = tmp_dir / "run_setup.bat"
        bat.write_text(
            f"""@echo off
setlocal
set PID={os.getpid()}
:wait
tasklist /FI "PID eq %PID%" 2>NUL | find "%PID%" >NUL
if not errorlevel 1 (
  timeout /t 1 /nobreak >NUL
  goto wait
)
start "" "{download_path}"
del "%~f0" >NUL 2>&1
""",
            encoding="utf-8",
        )
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        subprocess.Popen(["cmd.exe", "/c", str(bat)], creationflags=flags, close_fds=True)
        sys.exit(0)
    if lower.endswith(".deb"):
        status("Apertura pacchetto .deb…")
        opener = shutil.which("xdg-open") or shutil.which("gnome-open")
        if opener:
            subprocess.Popen([opener, str(download_path)], start_new_session=True)
        else:
            subprocess.Popen(["pkexec", "dpkg", "-i", str(download_path)], start_new_session=True)
        sys.exit(0)

    payload = _unpack_if_zip(download_path, tmp_dir)

    if sys.platform.startswith("win"):
        new_exe = payload if payload.is_file() else _find_in_tree(payload, ("WattbikeLogger*.exe", "*.exe"))
        if new_exe is None or not new_exe.is_file():
            raise RuntimeError("Nessun .exe trovato nel pacchetto di aggiornamento.")
        _install_windows(exe, new_exe, status)
    elif sys.platform == "darwin":
        _install_macos(exe, payload, status)
    else:
        new_bin = payload if payload.is_file() else _find_in_tree(payload, ("WattbikeLogger*",))
        if new_bin is None or not new_bin.is_file():
            raise RuntimeError("Nessun binario trovato nel pacchetto di aggiornamento.")
        _install_posix(exe, new_bin, status)


def _macos_app_bundle(exe: Path) -> Path | None:
    """Se exe è dentro Foo.app/Contents/MacOS/..., restituisce Foo.app."""
    parts = exe.resolve().parts
    for i, part in enumerate(parts):
        if part.endswith(".app"):
            return Path(*parts[: i + 1])
    return None


def _install_macos(current_exe: Path, payload: Path, status: Callable[[str], None]) -> None:
    status("Installazione aggiornamento macOS...")
    app = None
    if payload.is_dir() and payload.name.endswith(".app"):
        app = payload
    elif payload.is_dir():
        app = _find_in_tree(payload, ("WattbikeLogger.app", "*.app"))
        if app is not None and not str(app).endswith(".app"):
            app = None
        # rglob('*.app') returns the .app directory itself
        if app is None:
            for p in payload.rglob("*"):
                if p.is_dir() and p.name.endswith(".app"):
                    app = p
                    break

    current_app = _macos_app_bundle(current_exe)
    if app is not None and current_app is not None:
        dest = current_app
        # Preferisci /Applications se l'app corrente è lì o esiste già
        apps_dest = Path("/Applications") / app.name
        if str(current_app).startswith("/Applications") or apps_dest.exists():
            dest = apps_dest
        sh = Path(tempfile.mkdtemp(prefix="wattbike_upd_")) / "update.sh"
        sh.write_text(
            f"""#!/bin/bash
PID={os.getpid()}
while kill -0 "$PID" 2>/dev/null; do sleep 1; done
xattr -cr "{app}" 2>/dev/null || true
rm -rf "{dest}"
cp -R "{app}" "{dest}"
xattr -cr "{dest}" 2>/dev/null || true
open "{dest}"
rm -rf "{payload.parent}" 2>/dev/null || true
rm -f "$0"
""",
            encoding="utf-8",
        )
        os.chmod(sh, 0o755)
        subprocess.Popen(["/bin/bash", str(sh)], start_new_session=True)
        status("Riavvio in corso...")
        sys.exit(0)

    # Fallback: binario onefile
    new_bin = payload if payload.is_file() else _find_in_tree(payload, ("WattbikeLogger*",))
    if new_bin is None or not new_bin.is_file():
        raise RuntimeError("WattbikeLogger.app non trovato nel pacchetto.")
    _install_posix(current_exe, new_bin, status)


def _install_windows(current_exe: Path, new_file: Path, status: Callable[[str], None]) -> None:
    status("Preparazione installazione Windows...")
    bat = current_exe.with_suffix(".update.bat")
    # Attende la fine del PID, sostituisce, rilancia, cancella se stesso.
    script = f"""@echo off
setlocal
set PID={os.getpid()}
set TARGET={current_exe}
set NEW={new_file}
:wait
tasklist /FI "PID eq %PID%" 2>NUL | find "%PID%" >NUL
if not errorlevel 1 (
  timeout /t 1 /nobreak >NUL
  goto wait
)
copy /Y "%NEW%" "%TARGET%" >NUL
start "" "%TARGET%"
del "%NEW%" >NUL 2>&1
del "%~f0" >NUL 2>&1
"""
    bat.write_text(script, encoding="utf-8")
    # CREATE_NO_WINDOW = 0x08000000 — niente console flash
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat)],
        creationflags=flags,
        close_fds=True,
    )
    status("Riavvio in corso...")
    sys.exit(0)


def _install_posix(current_exe: Path, new_file: Path, status: Callable[[str], None]) -> None:
    status("Installazione aggiornamento...")
    # Su macOS/Linux l'exe in esecuzione può spesso essere sostituito su disco.
    backup = current_exe.with_suffix(current_exe.suffix + ".bak")
    try:
        if backup.exists():
            backup.unlink()
        shutil.copy2(current_exe, backup)
        shutil.copy2(new_file, current_exe)
        os.chmod(current_exe, 0o755)
        new_file.unlink(missing_ok=True)
    except OSError:
        # Fallback: script shell
        sh = current_exe.with_suffix(".update.sh")
        sh.write_text(
            f"""#!/bin/bash
PID={os.getpid()}
while kill -0 "$PID" 2>/dev/null; do sleep 1; done
cp -f "{new_file}" "{current_exe}"
chmod +x "{current_exe}"
rm -f "{new_file}"
nohup "{current_exe}" >/dev/null 2>&1 &
rm -f "$0"
""",
            encoding="utf-8",
        )
        os.chmod(sh, 0o755)
        subprocess.Popen(["/bin/bash", str(sh)], start_new_session=True)
        status("Riavvio in corso...")
        sys.exit(0)

    status("Riavvio...")
    os.execv(str(current_exe), [str(current_exe)])
