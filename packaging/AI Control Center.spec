# -*- mode: python ; coding: utf-8 -*-
#
# Rebuild with (from the repo root):
#   .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath dist --workpath build "packaging\AI Control Center.spec"
#
# Produces dist\AI Control Center\ (onedir build - launch AI Control Center.exe
# inside it directly to smoke-test before handing it to Inno Setup).

a = Analysis(
    ['..\\app\\main.py'],
    pathex=[],
    binaries=[],
    # The six redesigned panels are HTML rendered in a QWebEngineView. They are
    # data, not code, so PyInstaller does not find them by following imports -
    # leaving this empty produces an app whose panels are simply blank.
    # `app/ui/web_panel.py` looks for them under sys._MEIPASS when frozen, which
    # is where this puts them.
    datas=[('..\\app\\ui\\web\\*.html', 'app/ui/web'),
           ('..\\app\\ui\\web\\*.css', 'app/ui/web'),
           # shell.js drives the sidebar and top bar. It was missing until
           # 2026-09-25 (external review A13): the frozen build's shell was
           # blank while every source-tree check passed.
           ('..\\app\\ui\\web\\*.js', 'app/ui/web'),
           # The window/dialog icon. The exe's own icon comes from EXE(icon=)
           # below; this copy is what QApplication.setWindowIcon reads at
           # runtime, and without it every window falls back to Qt's default.
           ('aicc.ico', '.')],
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
    [],
    exclude_binaries=True,
    name='AI Control Center',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Built by tools/make_icon.py from packaging/icon-source.png. Rebuild the
    # .ico rather than editing it: the script crops the artwork's black margin
    # away and cuts the rounded corners out to transparent, without which the
    # tile renders as a plain black square on a light taskbar.
    icon='aicc.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AI Control Center',
)
