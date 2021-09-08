# -*- mode: python ; coding: utf-8 -*-

import os
import sys

from platform import system

cwd = os.getcwd()

site_package_paths = [path for path in sys.path if path.endswith('site-packages')]
if not site_package_paths:
    raise ValueError('There is no valid path for site-packages!')

block_cipher = None
a = Analysis(
    ['main.py'],
    pathex=site_package_paths,
    binaries=[],
    # Put data(i.e. assets) under virtual 'modi_firmware_updater/'
    datas=[
        ('modi_firmware_updater/assets', 'modi_firmware_updater/assets'),
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
    name='MODI Firmware Updater(교원 AS용 v2.0.0)',
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
    name='MODI Firmware Updater(교원 AS용 v2.0.0).app',
    icon='network_module.ico',
    bundle_identifier=None,
)

if system == 'Darwin':
    import plistlib
    from pathlib import Path

    app_path = Path(app.name)

    # read Info.plist
    with open(app_path / 'Contents/Info.plist', 'rb') as f:
        pl = plistlib.load(f)

    # write Info.plist
    with open(app_path / 'Contents/Info.plist', 'wb') as f:
        pl['CFBundleExecutable'] = 'wrapper'
        plistlib.dump(pl, f)

    # write new wrapper script
    shell_script = """#!/bin/bash
    dir=$(dirname $0)
    open -a Terminal file://${dir}/%s""" % app.appname
    with open(app_path / 'Contents/MacOS/wrapper', 'w') as f:
        f.write(shell_script)

    # make it executable
    (app_path  / 'Contents/MacOS/wrapper').chmod(0o755)
