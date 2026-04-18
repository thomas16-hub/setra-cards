# PyInstaller spec — Setra CARDS v2 (Flet)
from pathlib import Path

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"

# Recolectar datos que Flet necesita en runtime
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

flet_datas = collect_data_files("flet") + collect_data_files("flet_desktop")
flet_hidden = collect_submodules("flet") + collect_submodules("flet_desktop")

# Incluir el cliente de Flet (flet-windows.zip) pre-descargado para evitar
# que la app trate de bajarlo en el primer arranque (falla en PCs con SSL
# root certs desactualizados). flet_desktop lo detecta automáticamente en
# get_package_bin_dir() == flet_desktop/app/
flet_client_zip = ROOT / "build_exe" / "flet_client" / "flet-windows.zip"
flet_client_data = []
if flet_client_zip.exists():
    flet_client_data = [(str(flet_client_zip), "flet_desktop/app")]
else:
    raise FileNotFoundError(
        f"Falta {flet_client_zip}. Descargar desde "
        f"https://github.com/flet-dev/flet/releases/download/v0.84.0/flet-windows.zip"
    )

datas = list(flet_datas) + flet_client_data

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
