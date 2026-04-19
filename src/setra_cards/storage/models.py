"""SQLAlchemy models para Setra CARDS.

Esquema v2.0.0 — DB vacia al instalar. Cada hotel crea su info desde cero.

Cambios vs locstar-app v1:
- Tabla Guest separada (antes huesped iba embedded en CardLog).
- CardLog ahora referencia guest_id (nullable — staff/blank no tienen guest).
- Sin hotel_name en CardLog (la app es single-hotel por instalacion).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Operator(Base):
    """Operador (recepcion, admin). PIN hasheado con PBKDF2-SHA256."""

    __tablename__ = "operators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    pin_hash: Mapped[str] = mapped_column(String(128))
    pin_salt: Mapped[str] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(20), default="frontdesk")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_pin: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Room(Base):
    """Habitacion del hotel."""

    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sequential_id: Mapped[int] = mapped_column(Integer, unique=True)
    display_number: Mapped[str] = mapped_column(String(20), unique=True)
    building: Mapped[int] = mapped_column(Integer, default=1)
    floor: Mapped[int] = mapped_column(Integer, default=1)
    # room_no_id: identificador dentro del piso (1-255). La cerradura valida
    # contra (building, floor, room_no_id) — NO contra sequential_id.
    room_no_id: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(30), default="limpia")
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Guest(Base):
    """Huesped del hotel — persiste entre estancias (historico)."""

    __tablename__ = "guests"
    __table_args__ = (
        Index("ix_guests_document", "document"),
        Index("ix_guests_name", "name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    document: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)  # opcional, para futuro
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    cards: Mapped[list["CardLog"]] = relationship(back_populates="guest")


class CardLog(Base):
    """Registro de emisiones de tarjeta."""

    __tablename__ = "card_log"
    __table_args__ = (
        Index("ix_card_log_room_expires", "room_display", "expires_at"),
        Index("ix_card_log_issued", "issued_at"),
        Index("ix_card_log_type_success", "card_type", "success"),
        Index("ix_card_log_guest", "guest_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_type: Mapped[str] = mapped_column(String(20))  # Guest, Staff, Auth, Clock, Setting, Blank, Master
    card_type_byte: Mapped[int] = mapped_column(Integer)
    guest_id: Mapped[int | None] = mapped_column(ForeignKey("guests.id"), nullable=True)
    room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id"), nullable=True)
    room_display: Mapped[str | None] = mapped_column(String(20), nullable=True)
    uid_hex: Mapped[str | None] = mapped_column(String(20), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    operator: Mapped[str] = mapped_column(String(50), default="admin")
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    guest: Mapped["Guest | None"] = relationship(back_populates="cards")


class Staff(Base):
    """Empleado del hotel con rol y asignaciones."""

    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    role: Mapped[str] = mapped_column(String(30))  # recepcion, limpieza, mantenimiento, admin
    document: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    assigned_rooms: Mapped[str | None] = mapped_column(String(2000), nullable=True)  # JSON list of display_numbers
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    card_uid: Mapped[str | None] = mapped_column(String(20), nullable=True)
    card_expires: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ActionLog(Base):
    """Auditoria de acciones administrativas."""

    __tablename__ = "action_log"
    __table_args__ = (
        Index("ix_action_log_timestamp", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(60))
    operator: Mapped[str] = mapped_column(String(50))
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class LoginAttempt(Base):
    """Rate-limit de intentos fallidos — persistente en DB."""

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operator_name: Mapped[str] = mapped_column(String(50))
    attempted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class S70EventLog(Base):
    """Eventos leidos de tarjetas S70 (trazabilidad de aperturas en cerraduras)."""

    __tablename__ = "s70_events"
    __table_args__ = (
        Index("ix_s70_uid_card", "s70_uid_hex", "card_uid_hex"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    s70_uid_hex: Mapped[str] = mapped_column(String(20))
    card_number: Mapped[int] = mapped_column(Integer, default=0)
    event_type: Mapped[int] = mapped_column(Integer)
    event_type_name: Mapped[str] = mapped_column(String(50))
    card_uid_hex: Mapped[str] = mapped_column(String(20))
    event_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    extra: Mapped[int] = mapped_column(Integer, default=0)
    building: Mapped[int] = mapped_column(Integer, default=0)
    floor: Mapped[int] = mapped_column(Integer, default=0)
    read_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Setting(Base):
    """Configuracion key-value persistente (puerto COM, ultima actualizacion, etc)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(String(2000))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
