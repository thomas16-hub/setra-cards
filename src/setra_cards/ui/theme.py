"""Paleta de colores + constantes de spacing para Setra CARDS.

Identidad visual Grupo SETRA Holdings — dark theme (navy + gold).
"""
from __future__ import annotations

# ═══════════════════════════════════════════════════════════
# SETRA BRAND — Dark navy + gold
# ═══════════════════════════════════════════════════════════

# Fondos (navy degradado)
BG = "#0D1B2A"
BG_ALT = "#162230"
SURFACE = "#1B2838"
SURFACE_ALT = "#22334A"
SURFACE_ELEVATED = "#2C3E50"

# Bordes
BORDER = "#2C3E50"
BORDER_STRONG = "#3C5068"
BORDER_SUBTLE = "#1F2F44"

# Texto
TEXT = "#E8ECEF"
TEXT_MUTED = "#9BA8B4"
TEXT_LIGHT = "#6B7A8A"
TEXT_INVERSE = "#0D1B2A"

# Gold SETRA (acento primario)
GOLD = "#C9A84C"
GOLD_LIGHT = "#E8D298"
GOLD_DARK = "#A8882F"
GOLD_BG = "rgba(201,168,76,0.12)"

# Aliases para compatibilidad con código existente
PRIMARY = GOLD
PRIMARY_HOVER = GOLD_DARK
ACCENT = GOLD
ACCENT_HOVER = GOLD_DARK

# Estados
SUCCESS = "#3DCC7E"
SUCCESS_DARK = "#1A7A4A"
WARNING = "#FFB347"
WARNING_DARK = "#E67E22"
ERROR = "#FF6B5E"
ERROR_DARK = "#C0392B"
INFO = "#5DADE2"
INFO_DARK = "#2980B9"

# Badges por estado de habitación / rol
STATE_CLEAN = SUCCESS
STATE_DIRTY = WARNING
STATE_MAINTENANCE = ERROR
STATE_OCCUPIED = INFO
STATE_BLOCKED = TEXT_MUTED

# Tipografía
FONT_DISPLAY = "Playfair Display"
FONT_UI = "Inter"

# Radios y spacing
CARD_RADIUS = 14
BUTTON_RADIUS = 10
INPUT_RADIUS = 10
BADGE_RADIUS = 20

PADDING_SM = 8
PADDING_MD = 16
PADDING_LG = 24
PADDING_XL = 32
