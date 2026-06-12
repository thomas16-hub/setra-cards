# Setra CARDS

Aplicacion nativa de escritorio para programar tarjetas de cerraduras hoteleras Locstar.

Version 2.0 — reescrita en **Flet** (UI nativa Windows) tras migrar desde la
version web (FastAPI + htmx). Corre 100% local en cada PC del hotel, sin
depender de internet ni servidores externos.

## Stack

- **Python 3.11+**
- **Flet** — UI nativa multi-plataforma (Windows principal)
- **SQLAlchemy + SQLite** — persistencia local
- **PyInstaller** — empaquetado a `.exe`
- **pyserial + pycryptodome** — protocolo MF-NK (reverse-engineered)

## Instalacion para desarrollo

```bash
cd setra-cards
pip install -e .[dev]
python -m setra_cards.main
```

El primer arranque:
- Crea `%LOCALAPPDATA%\SETRA\SetraCARDS\` con la base de datos vacia
- Crea operador `Admin` con un PIN aleatorio de un solo uso (queda registrado
  una vez en `setra-cards.log`; la app exige cambiarlo en el primer login)
- Auto-detecta el encoder USB si esta conectado

Recuperacion de acceso: `Setra-CARDS.exe --reset-admin` imprime en consola un
PIN temporal nuevo para el super manager.

## Distribucion (build + instalador)

```bash
pyinstaller build_exe/setra-cards.spec --noconfirm --clean
# Genera dist/Setra-CARDS/Setra-CARDS.exe
```

El instalador (`Instalar.bat`) copia la app a `%LOCALAPPDATA%`,
crea acceso directo, registra arranque automatico, y deja el
`hotel.json` correspondiente al hotel elegido.

## Actualizaciones OTA

La instalacion de actualizaciones es **manual**: boton "Buscar actualizacion"
en Administracion. Al arrancar, la app hace ademas un chequeo best-effort en
segundo plano que solo deja constancia en el log si hay version nueva — nunca
descarga ni instala sola. Al instalar desde el boton:
1. Descarga el `.zip` del release
2. Verifica SHA-256 (obligatorio — sin hash publicado se rechaza)
3. Reemplaza archivos (preservando `hotel.json`, datos y configs por-PC) y reinicia

## Arquitectura

```
src/setra_cards/
├── cards/       # Protocolo MF-NK (crypto, reader, writer, s70)
├── encoder/     # Driver serial + handshake
├── storage/     # SQLAlchemy models + DB init
├── services/    # auth, encoder_service, card_service, reports
├── core/        # AppState singleton + HotelConfig
└── ui/
    ├── theme.py
    └── views/   # login, shell, dashboard, rooms, guests, cards, staff, admin
```

## Licencia

Propietario — SETRA Holdings.
