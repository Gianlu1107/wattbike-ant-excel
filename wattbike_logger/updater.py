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
    """Sceglie l'asset eseguibile per la piattaforma/architettura corrente."""
    names = [(a.get("name") or "", a) for a in assets]
    mach = platform.machine().lower()

    if sys.platform.startswith("win"):
        keys = ["windows-x64", "windows", "win"]
        if mach in ("arm64", "aarch64"):
            keys = ["windows-arm64", "windows-arm", "windows"]
        exts = (".exe",)
    elif sys.platform == "darwin":
        if mach in ("arm64", "aarch64"):
            keys = ["macos-arm64", "darwin-arm64", "macos-arm", "arm64"]
        else:
            keys = ["macos-x64", "macos-amd64", "macos-x86_64", "darwin-x64", "x86_64", "x64"]
        keys += ["macos", "darwin", "mac"]
        exts = ("", ".zip", ".dmg")  # onefile senza estensione
    else:
        if mach in ("aarch64", "arm64"):
            keys = ["linux-arm64", "linux-aarch64", "arm64"]
        else:
            keys = ["linux-x64", "linux-amd64", "linux-x86_64", "x86_64", "x64"]
        keys += ["linux"]
        exts = ("", ".AppImage", ".tar.gz", ".zip")

    def score(name: str) -> int:
        lower = name.lower()
        s = 0
        for i, key in enumerate(keys):
            if key in lower:
                s += 100 - i
        if any(lower.endswith(ext) for ext in exts if ext):
            s += 5
        if lower.endswith(".exe") and sys.platform.startswith("win"):
            s += 20
        if "wattbike" in lower:
            s += 3
        # penalizza asset chiaramente di altre piattaforme
        if sys.platform != "darwin" and "macos" in lower:
            s -= 50
        if not sys.platform.startswith("win") and lower.endswith(".exe"):
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


def apply_update(
    release: ReleaseInfo,
    on_progress: Callable[[int, int], None] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> None:
    """
    Scarica il nuovo eseguibile e lo installa sostituendo quello corrente.
    Su Windows usa uno script .bat che aspetta la chiusura del processo.
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

    if sys.platform.startswith("win"):
        _install_windows(exe, download_path, status)
    elif sys.platform == "darwin":
        _install_posix(exe, download_path, status)
    else:
        _install_posix(exe, download_path, status)


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
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat)],
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010),
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
