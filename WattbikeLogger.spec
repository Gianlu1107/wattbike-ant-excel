# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Wattbike ANT+ Logger (cross-platform)."""

import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

datas = [("wattbike_logger/resources", "wattbike_logger/resources")]
binaries = []
hiddenimports = [
    "usb",
    "usb.backend.libusb1",
    "usb.backend.libusb0",
    "openant",
    "openant.base",
    "openant.easy",
    "openant.devices",
    "matplotlib",
    "matplotlib.backends.backend_tkagg",
]

for pkg in ("openant", "usb", "matplotlib"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        hiddenimports += collect_submodules(pkg)

# Nome asset per piattaforma (sovrascrivibile da CI)
exe_name = os.environ.get("WATTBIKE_EXE_NAME", "WattbikeLogger")

a = Analysis(
    ["launch_gui.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
