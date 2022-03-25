# -*- mode: python ; coding: utf-8 -*-

import os
import sys

cwd = os.getcwd()

site_package_paths = [path for path in sys.path if path.endswith('site-packages')]
if not site_package_paths:
    raise ValueError('There is no valid path for site-packages!')

block_cipher = None
a = Analysis(
    ['main.py'],
    pathex=site_package_paths,
    binaries=[],
    datas=[
        ('modi_firmware_updater/assets', 'modi_firmware_updater/assets'),
        ('modi_firmware_updater/core', 'modi_firmware_updater/core'),
    ],
    hiddenimports=[
        "modi_firmware_updater.util.connection_util",
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
    name='MODI Firmware Updater',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon='network_module.ico',
)

app = BUNDLE(
    exe,
    name='MODI Firmware Updater.app',
    icon='network_module.ico',
    bundle_identifier=None,
)