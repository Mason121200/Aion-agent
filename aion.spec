# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

# 收集 Web 服务依赖（fastapi / uvicorn / starlette / pydantic 含动态导入）
_datas, _binaries, _hiddenimports = [], [], []
for _pkg in ("fastapi", "uvicorn", "starlette", "pydantic"):
    _d, _b, _h = collect_all(_pkg)
    _datas += _d
    _binaries += _b
    _hiddenimports += _h

# 打包 Web UI 静态资源
_UI_SRC = Path(SPECPATH) / "aion_agent" / "server" / "ui"
_datas += [(_UI_SRC.as_posix(), "aion_agent/server/ui")]

a = Analysis(
    ["packaging" + chr(92) + "launcher.py"],
    pathex=[],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["webview", "clr", "pythonnet", "bottle", "proxy_tools", "PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter"],
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
    name="aion",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
