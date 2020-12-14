# -*- mode: python ; coding: utf-8 -*-

import os
import sys

cwd = os.getcwd()
pyqt_ui = os.path.join(cwd, 'modi', 'assets', 'modi_firmware_updater.ui')
esp32_bins = os.path.join(cwd, 'modi', 'assets', 'firmware', 'esp32', '*')
stm32_bins = os.path.join(cwd, 'modi', 'assets', 'firmware', 'stm32', '*')

site_package_paths = [path for path in sys.path if path.endswith('site-packages')]


block_cipher = None
a = Analysis(
    ['main.py'],
    pathex=[cwd].extend(site_package_paths),
    binaries=[],
    # Put data(i.e. assets) under virtual 'modi/'
    datas=[
        (pyqt_ui, 'modi'),
        (esp32_bins, 'modi'),
        (stm32_bins, 'modi'),
    ],
    hiddenimports=[
        "modi.task.ser_task",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False
)
pyz = PYZ(
    a.pure, a.zipped_data, cipher=block_cipher
)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='modi_updater',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
)
app = BUNDLE(
    exe,
    name='modi_updater.app',
    icon=None,
    bundle_identifier=None,
)
