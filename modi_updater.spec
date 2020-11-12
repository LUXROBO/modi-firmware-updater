# -*- mode: python ; coding: utf-8 -*-

import os

cwd = os.getcwd()
pyqt_ui = os.path.join(cwd, 'modi', 'assets', 'modi_firmware_updater.ui')
pyqt_imgs = os.path.join(cwd, 'modi', 'assets', 'image', '*')
esp32_bins = os.path.join(cwd, 'modi', 'assets', 'firmware', 'esp32', '*')
stm32_bins = os.path.join(cwd, 'modi', 'assets', 'firmware', 'stm32', '*')

block_cipher = None


a = Analysis(
    ['main.py'],
    pathex=[cwd],
    binaries=[],
    # Put data(i.e. assets) under virtual 'modi/'
    datas=[
        (pyqt_ui, 'modi'),
        (pyqt_imgs, 'modi'),
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
    name='modi_updater.exe',
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
