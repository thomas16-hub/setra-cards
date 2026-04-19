"""CRUD de habitaciones."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from setra_cards.storage.models import Room

VALID_STATES = {"limpia", "sucia", "inspeccion", "mantenimiento", "fuera_de_servicio"}


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


def _next_room_no_id(session: Session, building: int, floor: int) -> int:
    """Siguiente room_no_id libre dentro del (building, floor).

    La cerradura valida por esta terna, por eso se reinicia por piso.
    """
    result = session.execute(
        select(func.max(Room.room_no_id)).where(
            Room.building == building,
            Room.floor == floor,
        )
    ).scalar()
    return (result or 0) + 1


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
    # Si cambió building o floor, recalcular room_no_id (nuevo piso → nueva serie)
    if building_changed or floor_changed:
        r.room_no_id = _next_room_no_id(session, r.building, r.floor)
    if state is not None:
        if state not in VALID_STATES:
            raise ValueError(f"Estado invalido: {state}")
        r.state = state
    if notes is not None:
        r.notes = notes
    session.commit()
    return r


def set_state(session: Session, room_id: int, state: str) -> Room:
    return update_room(session, room_id, state=state)


def delete_room(session: Session, room_id: int) -> None:
    r = session.get(Room, room_id)
    if r:
        session.delete(r)
        session.commit()
