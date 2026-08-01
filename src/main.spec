# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'win32gui',
        'win32api',
        'win32con', 
        'win32com.client',
        'winshell',
        'winreg',
        'screen_brightness_control',
        'pywinstyles',
        'sv_ttk',
        'darkdetect',
        'pystray',
        'lcu_driver',
        'win32_window_monitor',
        'packaging',
        'PIL',
        'requests',
        'appdirs',
        'json',
        'base64',
        'io'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='QOL-scripts',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
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
