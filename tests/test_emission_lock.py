"""Service-level emission lock tests.

The encoder per-command lock (driver._lock) only serializes a single serial
frame. A full card write is ~20 commands; two operations (a double-click that
slips past a UI busy flag, or the watchdog/connect_port tearing down the port
mid-write) could otherwise interleave commands or close the port between
commands, corrupting a card that still ACKs every block.

These tests assert the OUTER service-level lock (`encoder_service._emission_lock`,
held via `emission_transaction()`) serializes WHOLE operations:

  (a) a second emit while one is in progress fails fast with the Spanish busy
      message;
  (b) the watchdog tick is skipped (never calls connect_port) while an emission
      holds the lock;
  (c) no deadlock — the lock is released on normal completion AND on error;
  (d) connect_port is excluded during an in-flight emission (waits for it).

Determinism: a fake write_card blocks on a threading.Event so the "in-flight"
window is held open precisely while the assertions run, instead of relying on
sleeps racing the test.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import setra_cards.services.card_service as cs
from setra_cards.cards.crypto import DEFAULT_SIGNATURE
from setra_cards.cards.writer import WriteResult
from setra_cards.core.app_state import HotelConfig
from setra_cards.services import encoder_service
from setra_cards.services.card_service import BUSY_ERROR
from setra_cards.storage.models import Base, Room

UID = b"\x04\x01\x02\x03"


@pytest.fixture()
def hotel():
    return HotelConfig(
        name="Hotel Test", short_name="Test", city="Medellin",
        signature=DEFAULT_SIGNATURE, des_key=b"god\x00\x00\x00\x00\x00",
    )


@pytest.fixture()
def session():
    # StaticPool + check_same_thread=False so the worker thread (card write) and
    # the main thread (assertions / fail-fast second op) share one in-memory DB.
    engine = create_engine(
        "sqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture()
def encoder():
    return object()


@pytest.fixture(autouse=True)
def _release_lock_between_tests():
    """A leaked acquire would wedge every later test — assert a clean slate."""
    yield
    # The RLock must be fully released after each test.
    assert encoder_service._emission_lock.acquire(blocking=False), (
        "emission lock leaked — an operation did not release it"
    )
    encoder_service._emission_lock.release()


def _add_room(session, seq=1, display="101", room_no_id=1) -> Room:
    room = Room(sequential_id=seq, display_number=display,
                building=1, floor=1, room_no_id=room_no_id)
    session.add(room)
    session.commit()
    return room


# --- (a) second emit fails fast with the Spanish busy message ---------------

def test_second_emit_fails_fast_while_one_in_progress(monkeypatch, encoder, hotel, session):
    """A master write is held open by a blocking fake; a concurrent guest write
    must fail fast (not wait) with the Spanish busy message."""
    in_write = threading.Event()
    release_write = threading.Event()
    room = _add_room(session)

    def _blocking_write(*a, **k):
        in_write.set()
        # Hold the operation "in flight" until the test releases it.
        assert release_write.wait(timeout=5.0), "test never released the write"
        return WriteResult(True, UID)

    monkeypatch.setattr(cs, "write_card", _blocking_write)
    monkeypatch.setattr(cs, "build_master_card", lambda **k: b"\x00" * 48)

    result_holder = {}

    def _run_master():
        result_holder["master"] = cs.create_master_card(
            encoder=encoder, hotel=hotel, session=session,
            valid_until=datetime.now() + timedelta(days=1), operator="op1",
        )

    t = threading.Thread(target=_run_master, name="master-write")
    t.start()
    try:
        assert in_write.wait(timeout=5.0), "master write never started"

        # While the master write holds the lock, a guest write must fail fast.
        guest_result = cs.create_guest_card(
            encoder=encoder, hotel=hotel, session=session, room=room, guest=None,
            valid_from=datetime.now(), valid_until=datetime.now() + timedelta(days=1),
            operator="op2",
        )
        assert guest_result.ok is False
        assert guest_result.error == BUSY_ERROR
    finally:
        release_write.set()
        t.join(timeout=5.0)

    assert result_holder["master"].ok is True  # first op completed normally


# --- (b) watchdog tick is skipped while an emission holds the lock ----------

def test_watchdog_tick_skipped_during_emission(monkeypatch):
    """A held emission must make the watchdog tick bail without ever calling
    connect_port (it must not tear down the port mid-write)."""
    connect_calls = []
    auto_calls = []
    monkeypatch.setattr(
        encoder_service, "connect_port",
        lambda state, port: connect_calls.append(port) or (True, "ok"),
    )
    monkeypatch.setattr(
        encoder_service, "auto_connect",
        lambda state, hint_only=False: auto_calls.append(hint_only) or (True, "ok"),
    )

    state = SimpleNamespace(encoder=None, encoder_port="COM3")

    # The emission must be held by ANOTHER thread: the RLock is reentrant, so
    # holding it on the watchdog's own thread would (correctly) let the tick run.
    holder_has_lock = threading.Event()
    release = threading.Event()

    def _hold_emission():
        with encoder_service.emission_transaction(blocking=True) as ok:
            assert ok
            holder_has_lock.set()
            assert release.wait(timeout=5.0)

    holder = threading.Thread(target=_hold_emission, name="emission-holder")
    holder.start()
    try:
        assert holder_has_lock.wait(timeout=5.0)
        remembered = encoder_service._watchdog_tick(state, "COM3")
    finally:
        release.set()
        holder.join(timeout=5.0)

    assert connect_calls == []  # NEVER touched the port during the emission
    assert auto_calls == []
    assert remembered == "COM3"  # last-known port preserved for the next tick


def test_watchdog_tick_runs_when_lock_free(monkeypatch):
    """Sanity: when no emission holds the lock, the tick reconnects normally."""
    connect_calls = []
    monkeypatch.setattr(
        encoder_service, "connect_port",
        lambda state, port: connect_calls.append(port) or (True, "ok"),
    )
    state = SimpleNamespace(encoder=None, encoder_port="COM3")
    encoder_service._watchdog_tick(state, "COM3")
    assert connect_calls == ["COM3"]


# --- (c) no deadlock: lock released on normal completion AND on error -------

def test_lock_released_after_normal_completion(monkeypatch, encoder, hotel, session):
    monkeypatch.setattr(cs, "write_card", lambda *a, **k: WriteResult(True, UID))
    monkeypatch.setattr(cs, "build_clock_card", lambda **k: b"\x00" * 48)

    result = cs.create_clock_card(encoder=encoder, hotel=hotel, session=session, operator="op")
    assert result.ok is True
    # Lock must be free again — re-acquire non-blocking proves it.
    assert encoder_service._emission_lock.acquire(blocking=False)
    encoder_service._emission_lock.release()


def test_lock_released_after_hardware_error(monkeypatch, encoder, hotel, session):
    monkeypatch.setattr(cs, "write_card", lambda *a, **k: WriteResult(False, None, error="boom"))
    monkeypatch.setattr(cs, "build_clock_card", lambda **k: b"\x00" * 48)

    result = cs.create_clock_card(encoder=encoder, hotel=hotel, session=session, operator="op")
    assert result.ok is False
    assert encoder_service._emission_lock.acquire(blocking=False)
    encoder_service._emission_lock.release()


def test_lock_released_after_db_failure(monkeypatch, encoder, hotel, session):
    """Even when the post-write DB audit raises, the lock must be released."""
    monkeypatch.setattr(cs, "write_card", lambda *a, **k: WriteResult(True, UID))
    monkeypatch.setattr(cs, "build_clock_card", lambda **k: b"\x00" * 48)

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(cs, "_log_emission", _boom)

    result = cs.create_clock_card(encoder=encoder, hotel=hotel, session=session, operator="op")
    assert result.ok is False
    assert "base de datos" in result.error
    assert encoder_service._emission_lock.acquire(blocking=False)
    encoder_service._emission_lock.release()


# --- (d) connect_port is excluded during an in-flight emission --------------

def test_connect_port_waits_for_in_flight_emission(monkeypatch, encoder, hotel, session):
    """connect_port (blocking) must not proceed until the in-flight emission
    releases the lock — proving the port can't be swapped mid-write."""
    in_write = threading.Event()
    release_write = threading.Event()
    room = _add_room(session)

    order: list[str] = []
    order_lock = threading.Lock()

    def _record(tag: str) -> None:
        with order_lock:
            order.append(tag)

    def _blocking_write(*a, **k):
        _record("write_start")
        in_write.set()
        assert release_write.wait(timeout=5.0)
        _record("write_end")
        return WriteResult(True, UID)

    monkeypatch.setattr(cs, "write_card", _blocking_write)

    # Fake the driver so connect_port's open()/beep() succeed instantly.
    class _FakeDriver:
        def __init__(self, port):
            self.port = port

        def open(self):
            pass

        def beep(self):
            _record("connect_proceeded")
            return True

        def close(self):
            pass

    monkeypatch.setattr(encoder_service, "EncoderDriver", _FakeDriver)

    state = SimpleNamespace(encoder=None, encoder_port=None,
                            set_encoder=lambda enc, port: None,
                            clear_encoder=lambda: None)

    def _run_guest():
        cs.create_guest_card(
            encoder=encoder, hotel=hotel, session=session, room=room, guest=None,
            valid_from=datetime.now(), valid_until=datetime.now() + timedelta(days=1),
            operator="op1",
        )

    writer = threading.Thread(target=_run_guest, name="guest-write")
    writer.start()
    try:
        assert in_write.wait(timeout=5.0), "guest write never started"

        connected = threading.Event()

        def _run_connect():
            encoder_service.connect_port(state, "COM5")
            connected.set()

        connector = threading.Thread(target=_run_connect, name="connect")
        connector.start()
        try:
            # connect_port must still be blocked behind the in-flight write.
            assert not connected.wait(timeout=0.3), "connect_port ran during emission!"
            with order_lock:
                assert "connect_proceeded" not in order

            release_write.set()  # let the write finish -> lock released
            assert connected.wait(timeout=5.0), "connect_port never resumed"
            with order_lock:
                # connect only proceeded AFTER the write finished.
                assert order.index("write_end") < order.index("connect_proceeded")
        finally:
            connector.join(timeout=5.0)
    finally:
        release_write.set()
        writer.join(timeout=5.0)


# --- driver.transaction() delegates to the same service-level lock ----------

def test_driver_transaction_shares_service_lock():
    """driver.transaction() must take the SAME outer lock as emission_transaction
    (so it serializes against connect/disconnect/watchdog), not a private one."""
    from setra_cards.encoder.driver import EncoderDriver

    drv = EncoderDriver("COM_TEST")
    with drv.transaction(blocking=True) as acquired:
        assert acquired is True
        # While held, a non-blocking service acquire from "another operation"
        # perspective: same thread RLock would re-enter, so prove cross-context
        # by checking the lock is owned (re-acquire succeeds reentrantly here).
        with encoder_service.emission_transaction(blocking=False) as nested:
            assert nested is True  # reentrant on same thread


def test_driver_transaction_nonblocking_fails_when_held_by_other_thread():
    """driver.transaction(blocking=False) must report False if another THREAD
    already holds the emission lock."""
    from setra_cards.encoder.driver import EncoderDriver

    drv = EncoderDriver("COM_TEST")
    holder_has_lock = threading.Event()
    release = threading.Event()

    def _hold():
        with encoder_service.emission_transaction(blocking=True) as ok:
            assert ok
            holder_has_lock.set()
            assert release.wait(timeout=5.0)

    holder = threading.Thread(target=_hold, name="lock-holder")
    holder.start()
    try:
        assert holder_has_lock.wait(timeout=5.0)
        with drv.transaction(blocking=False) as acquired:
            assert acquired is False  # held by the other thread -> fail fast
    finally:
        release.set()
        holder.join(timeout=5.0)
