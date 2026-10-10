# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

# CustomTkinter keeps its themes and fonts as data files, so PyInstaller must
# be told to copy them into the .exe or the app will not start.
ctk_datas = collect_data_files("customtkinter")


a = Analysis(
    ['Amanuensis.py'],
    pathex=[],
    binaries=[],
    datas=ctk_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='amanuensis',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed programs are often flagged by antivirus tools, so keep it off
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
