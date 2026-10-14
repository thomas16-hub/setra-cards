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

> **Regla: toda version se publica SIEMPRE en el canal firmado** (y ademas en
> GitHub Releases). Nunca solo en GitHub: la v2.1.0 consulta unicamente el canal
> firmado y, sin el, queda sin poder actualizarse ("Canal de actualizacion no
> disponible", 2026-10-14). Se publica con un solo comando:
> `python build_exe/publish_release.py X.Y.Z --notes "..."` (ver abajo).

Canales, en el orden en que los consulta la app:

| Canal | URL | Lo leen |
|---|---|---|
| Manifest firmado (principal) | `https://portonsabaneta.com/setra-cards-flet/latest.json` | v2.1.0 (unico canal) y v2.1.2+ |
| GitHub Releases (respaldo) | `api.github.com/repos/thomas16-hub/setra-cards/releases/latest` | v2.0.x y v2.1.2+ si el manifest no responde |

GitHub solo es alcanzable por la app mientras el repo esta **publico**: se abre
durante la ventana de actualizacion y se vuelve a cerrar.

Cada release del canal principal se **firma con una llave Ed25519** cuyo secreto
vive solo en `~/.claude/credentials/` (nunca en el repo ni en un PC del hotel):
la app lleva embebida solo la llave **publica** de 32 bytes.

El manifest tiene la forma:

```json
{
  "version": "2.1.2",
  "url": "https://portonsabaneta.com/setra-cards-flet/setra-cards-v2.1.2-update.zip",
  "sha256": "<64 hex>",
  "signature": "<128 hex Ed25519>",
  "notes": "..."
}
```

La firma cubre el mensaje canonico `f"{version}|{url}|{sha256}"`.

La instalacion es **manual**: boton "Buscar actualizacion" en Administracion. Al
arrancar, la app hace ademas un chequeo best-effort en segundo plano que solo
deja constancia en el log si hay version nueva — nunca descarga ni instala sola.
El flujo del canal firmado, en orden (si `latest.json` no responde, v2.1.2+
pasa a GitHub Releases):
1. Descarga `latest.json` por HTTPS (URL fija codificada en la app)
2. **Verifica la firma Ed25519** con la llave publica embebida ANTES de confiar
   en cualquier campo; si falla, se rechaza con "Firma del manifest invalida"
3. Verifica que `url` empiece exactamente por el prefijo confiable
   `https://portonsabaneta.com/setra-cards-flet/` (guard anti-SSRF)
4. Descarga el `.zip` y verifica el SHA-256 publicado (obligatorio)
5. **Re-verifica la firma** antes de lanzar el swap
6. Reemplaza archivos (preservando `hoteles/`, `hotel.json`, datos y configs
   por-PC) y reinicia

Las releases de GitHub no llevan firma: su integridad depende de HTTPS y del
`sha256: <hash>` obligatorio en el cuerpo del release (reglas de la v2.0.10).

### Publicar una actualizacion

1. Subir `__version__` en `src/setra_cards/__init__.py` a la version nueva.
2. Si el cambio toca `encoder/` o `cards/writer.py`, **probarlo antes en el
   encoder real del hotel** (el simulador no reemplaza el aparato: la v2.1.0
   paso 275 tests y fallo en todas las grabaciones reales).
3. Ensayo sin publicar nada:
   `python build_exe/publish_release.py X.Y.Z --notes "..." --dry-run`
4. Publicar:
   `python build_exe/publish_release.py X.Y.Z --notes "Que cambia, en español"`

   El script corre los tests, compila, arma `setra-cards-vX.Y.Z-update.zip`
   (carpeta `Setra-CARDS/` + `Desinstalar.bat` + `Reset_PIN_Admin.bat`), firma,
   sube por FTP el ZIP y despues `latest.json`, relee el manifest en vivo con
   el mismo chequeo de la app y crea el release de GitHub marcado como latest.
5. Abrir el repo (`gh repo edit thomas16-hub/setra-cards --visibility public
   --accept-visibility-change-consequences`), actualizar los PCs desde
   Administracion → "Buscar actualizacion" y volver a cerrarlo
   (`--visibility private`).

### Si una version sale mal

- **En el PC:** la actualizacion deja la version anterior en
  `%LOCALAPPDATA%\SetraCARDS.prev`. Volver a ella: cerrar la app y renombrar
  `SetraCARDS` → `SetraCARDS.malo` y `SetraCARDS.prev` → `SetraCARDS`. Los datos
  (`%LOCALAPPDATA%\SETRA\SetraCARDS\`) no se tocan.
- **En los canales:** la app solo ofrece versiones MAYORES a la instalada, asi
  que no se "despublica": se publica una version nueva con el arreglo. En
  GitHub, marcar la version mala como prerelease
  (`gh release edit vX.Y.Z --prerelease`) para que deje de ser la latest.

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
