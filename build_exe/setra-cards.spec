# PyInstaller spec — Setra CARDS v2 (Flet)
from pathlib import Path

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"

# Recolectar datos que Flet necesita en runtime
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

flet_datas = collect_data_files("flet") + collect_data_files("flet_desktop")
flet_hidden = collect_submodules("flet") + collect_submodules("flet_desktop")

datas = list(flet_datas)

hiddenimports = list(flet_hidden) + [
    "serial.tools.list_ports",
    "sqlalchemy.dialects.sqlite",
    "Crypto.Cipher.DES",
    "Crypto.Cipher.AES",
]

a = Analysis(
    [str(ROOT / "build_exe" / "run_app.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy.testing", "pytest"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Setra-CARDS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # app nativa, sin consola
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Setra-CARDS",
)
