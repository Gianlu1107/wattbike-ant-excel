# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Wattbike ANT+ Logger (lean, no matplotlib/numpy)."""

import os
import sys

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

datas = [("wattbike_logger/resources", "wattbike_logger/resources")]
binaries = []
hiddenimports = [
    "usb",
    "usb.backend.libusb1",
    "usb.backend.libusb0",
    "usb.core",
    "usb.util",
    "openant",
    "openant.base",
    "openant.base.driver",
    "openant.easy",
    "openant.easy.node",
    "openant.easy.channel",
    "openant.devices",
    "openant.devices.common",
    "openant.devices.power_meter",
    "openant.devices.scanner",
]

try:
    hiddenimports += collect_submodules("openant")
except Exception:
    pass

# Esclude pacchetti pesanti / inutili in GUI
excludes = [
    "matplotlib",
    "mpl_toolkits",
    "numpy",
    "pandas",
    "PIL",
    "Pillow",
    "scipy",
    "skimage",
    "sklearn",
    "torch",
    "tensorflow",
    "IPython",
    "notebook",
    "pytest",
    "unittest",
    "test",
    "tests",
    "tkinter.test",
    "pydoc",
    "doctest",
    "xmlrpc",
    "multiprocessing.dummy",
]

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
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Rimuovi eventuali resti matplotlib/numpy se agganciati da hook
a.binaries = [b for b in a.binaries if not any(x in b[0].lower() for x in ("matplotlib", "numpy", "pandas"))]
a.datas = [d for d in a.datas if not any(x in str(d[0]).lower() for x in ("matplotlib", "numpy", "mpl-data"))]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# strip su Unix riduce un po' la size; su Windows spesso non disponibile
do_strip = sys.platform != "win32"

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
    strip=do_strip,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
