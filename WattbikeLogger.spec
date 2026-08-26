# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Wattbike ANT+ Logger (lean; .app onedir su macOS)."""

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
]

exe_name = os.environ.get("WATTBIKE_EXE_NAME", "WattbikeLogger")
# Su macOS il nome interno del binario nel .app resta WattbikeLogger
inner_name = "WattbikeLogger" if sys.platform == "darwin" else exe_name
is_macos = sys.platform == "darwin"

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

a.binaries = [b for b in a.binaries if not any(x in b[0].lower() for x in ("matplotlib", "numpy", "pandas"))]
a.datas = [d for d in a.datas if not any(x in str(d[0]).lower() for x in ("matplotlib", "numpy", "mpl-data"))]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
do_strip = sys.platform != "win32"

if is_macos:
    # onedir + BUNDLE: corretto per .app (onefile+BUNDLE deprecato)
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=inner_name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=do_strip,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=True,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=do_strip,
        upx=False,
        upx_exclude=[],
        name=inner_name,
    )
    app = BUNDLE(
        coll,
        name="WattbikeLogger.app",
        icon=None,
        bundle_identifier="com.gianlu.wattbikelogger",
        info_plist={
            "CFBundleName": "Wattbike Logger",
            "CFBundleDisplayName": "Wattbike Logger",
            "CFBundleShortVersionString": "1.3.3",
            "CFBundleVersion": "1.3.3",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "NSAppleEventsUsageDescription": "Wattbike Logger",
        },
    )
else:
    # Windows / Linux: onefile windowed (niente console)
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name=inner_name,
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
