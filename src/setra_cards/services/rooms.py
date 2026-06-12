"""CRUD de habitaciones."""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from setra_cards.services import action_log
from setra_cards.storage.models import Room

logger = logging.getLogger(__name__)

VALID_STATES = {"limpia", "sucia", "inspeccion", "mantenimiento", "fuera_de_servicio"}

# room_no_id travels in a single byte of block45 (byte[14]) — hard protocol cap
ROOM_NO_ID_MAX = 255


def list_rooms(session: Session) -> list[Room]:
    return session.execute(select(Room).order_by(Room.sequential_id)).scalars().all()


def get_room(session: Session, room_id: int) -> Room | None:
    return session.get(Room, room_id)


def get_by_display(session: Session, display_number: str) -> Room | None:
    return session.execute(
        select(Room).where(Room.display_number == display_number.strip())
    ).scalar_one_or_none()


def _next_sequential_id(session: Session) -> int:
    result = session.execute(select(func.max(Room.sequential_id))).scalar()
    return (result or 0) + 1


def _next_room_no_id(
    session: Session,
    building: int,
    floor: int,
    exclude_room_id: int | None = None,
) -> int:
    """Next free room_no_id within (building, floor).

    The lock validates against this triple, so the series restarts per floor.
    `exclude_room_id` skips the room being moved — otherwise its own current
    room_no_id would inflate the max() when it changes floors.
    """
    conditions = [Room.building == building, Room.floor == floor]
    if exclude_room_id is not None:
        conditions.append(Room.id != exclude_room_id)
    result = session.execute(
        select(func.max(Room.room_no_id)).where(*conditions)
    ).scalar()
    next_id = (result or 0) + 1
    if next_id > ROOM_NO_ID_MAX:
        raise ValueError(
            f"No hay room_no_id disponible en edificio {building}, piso {floor}: "
            f"se alcanzó el máximo de {ROOM_NO_ID_MAX} habitaciones por piso"
        )
    return next_id


def _validate_byte_field(value: int, name: str) -> None:
    if not (0 <= value <= 255):
        raise ValueError(f"{name} debe estar entre 0 y 255")


def create_room(
    session: Session,
    display_number: str,
    building: int = 1,
    floor: int = 1,
    state: str = "limpia",
    notes: str | None = None,
) -> Room:
    display = display_number.strip()
    if not display:
        raise ValueError("Numero de habitacion requerido")
    _validate_byte_field(building, "Edificio")
    _validate_byte_field(floor, "Piso")
    if state not in VALID_STATES:
        raise ValueError(f"Estado invalido: {state}")
    if get_by_display(session, display):
        raise ValueError(f"Ya existe habitacion {display}")
    # Retry en caso de race en sequential_id (UNIQUE constraint)
    last_exc: Exception | None = None
    for _ in range(3):
        r = Room(
            sequential_id=_next_sequential_id(session),
            display_number=display,
            building=building,
            floor=floor,
            room_no_id=_next_room_no_id(session, building, floor),
            state=state,
            notes=notes,
        )
        session.add(r)
        try:
            session.commit()
            session.refresh(r)
            return r
        except IntegrityError as exc:
            session.rollback()
            last_exc = exc
            continue
    raise ValueError(f"No se pudo crear la habitacion (conflicto de ID): {last_exc}")


def update_room(
    session: Session,
    room_id: int,
    *,
    display_number: str | None = None,
    building: int | None = None,
    floor: int | None = None,
    state: str | None = None,
    notes: str | None = None,
    operator: str = "system",
) -> Room:
    r = session.get(Room, room_id)
    if not r:
        raise ValueError("Habitacion no existe")
    if display_number is not None:
        new_display = display_number.strip()
        if not new_display:
            raise ValueError("Numero de habitacion no puede estar vacio")
        if new_display != r.display_number:
            if get_by_display(session, new_display):
                raise ValueError(f"Ya existe habitacion {new_display}")
            r.display_number = new_display
    old_triple = (r.building, r.floor, r.room_no_id)
    building_changed = False
    floor_changed = False
    if building is not None:
        _validate_byte_field(building, "Edificio")
        if building != r.building:
            building_changed = True
        r.building = building
    if floor is not None:
        _validate_byte_field(floor, "Piso")
        if floor != r.floor:
            floor_changed = True
        r.floor = floor
    # If building or floor changed, recompute room_no_id (new floor → new series),
    # excluding the room itself so its old id does not inflate the max().
    # no_autoflush: the SELECT must not flush the half-applied move (old room_no_id
    # on the new floor), which would transiently violate the UNIQUE(building,floor,
    # room_no_id) constraint. The final consistent triple is flushed at commit.
    if building_changed or floor_changed:
        with session.no_autoflush:
            r.room_no_id = _next_room_no_id(session, r.building, r.floor, exclude_room_id=r.id)
    if state is not None:
        if state not in VALID_STATES:
            raise ValueError(f"Estado invalido: {state}")
        r.state = state
    if notes is not None:
        r.notes = notes
    new_triple = (r.building, r.floor, r.room_no_id)
    session.commit()
    if new_triple != old_triple:
        # Audit trail: the lock validates against this triple — a change means
        # previously issued cards for this room stop opening the door.
        detail = (
            f"Habitacion {r.display_number}: (edificio, piso, room_no_id) "
            f"{old_triple} -> {new_triple}"
        )
        logger.info("Room lock-identity changed — %s", detail)
        action_log.log(session, "room_lock_id_changed", operator, detail)
    return r


def set_state(session: Session, room_id: int, state: str) -> Room:
    return update_room(session, room_id, state=state)


def delete_room(session: Session, room_id: int) -> None:
    r = session.get(Room, room_id)
    if r:
        session.delete(r)
        session.commit()
