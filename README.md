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

El canal de actualizaciones es un **manifest firmado** alojado en
`https://portonsabaneta.com/setra-cards-flet/latest.json` (el viejo canal de
GitHub Releases dejo de funcionar al volverse privado el repo). Cada release se
**firma OFFLINE con una llave Ed25519** cuyo secreto nunca toca ningun PC: la
app solo lleva embebida la llave **publica** de 32 bytes.

El manifest tiene la forma:

```json
{
  "version": "2.1.0",
  "url": "https://portonsabaneta.com/setra-cards-flet/setra-cards-v2.1.0.zip",
  "sha256": "<64 hex>",
  "signature": "<128 hex Ed25519>",
  "notes": "..."
}
```

La firma cubre el mensaje canonico `f"{version}|{url}|{sha256}"`.

La instalacion es **manual**: boton "Buscar actualizacion" en Administracion. Al
arrancar, la app hace ademas un chequeo best-effort en segundo plano que solo
deja constancia en el log si hay version nueva — nunca descarga ni instala sola.
El flujo, en orden:
1. Descarga `latest.json` por HTTPS (URL fija codificada en la app)
2. **Verifica la firma Ed25519** con la llave publica embebida ANTES de confiar
   en cualquier campo; si falla, se rechaza con "Firma del manifest invalida"
3. Verifica que `url` empiece exactamente por el prefijo confiable
   `https://portonsabaneta.com/setra-cards-flet/` (guard anti-SSRF)
4. Descarga el `.zip` y verifica el SHA-256 publicado (obligatorio)
5. **Re-verifica la firma** antes de lanzar el swap
6. Reemplaza archivos (preservando `hoteles/`, `hotel.json`, datos y configs
   por-PC) y reinicia

### Publicar una actualizacion (procedimiento manual)

1. Construir el `.zip` del release (ver seccion Distribucion / `release.yml`).
2. Calcular el SHA-256 del ZIP:
   `(Get-FileHash setra-cards-vX.Y.Z.zip -Algorithm SHA256).Hash.ToLower()`
3. **Firmar OFFLINE** el mensaje canonico `f"{version}|{url}|{sha256}"` con la
   llave privada Ed25519 (guardada en `~/.claude/credentials/`, NUNCA en el
   repo ni en ningun PC del hotel). El script de publicacion en `build_exe/`
   imprime el mensaje canonico exacto a firmar.
4. Construir `latest.json` con `{version, url, sha256, signature, notes}`.
5. **Subir por FTP** el ZIP y `latest.json` a `/setra-cards-flet/` en
   portonsabaneta.com (mismo metodo FTP directo que las landings).

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
