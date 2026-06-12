"""encoder_service tests — beep handshake rejection, hint-only auto-scan,
watchdog last-port preservation, pause/stop robustness."""
import pytest

from setra_cards.services import encoder_service
from setra_cards.services.encoder_service import ComPortInfo


class FakeState:
    """Duck-typed AppState substitute (encoder slots only)."""

    def __init__(self, encoder=None, encoder_port=None):
        self.encoder = encoder
        self.encoder_port = encoder_port

    def set_encoder(self, enc, port):
        self.encoder = enc
        self.encoder_port = port

    def clear_encoder(self):
        self.encoder = None
        self.encoder_port = None


def _patch_driver(monkeypatch, *, beep_ok=True, open_exc=None):
    """Replace EncoderDriver in encoder_service with a scripted fake."""
    created = []

    class _FakeDriver:
        def __init__(self, port):
            self.port = port
            self.closed = False
            created.append(self)

        def open(self):
            if open_exc is not None:
                raise open_exc

        def beep(self):
            return beep_ok

        def close(self):
            self.closed = True

        @property
        def is_connected(self):
            return not self.closed

    monkeypatch.setattr(encoder_service, "EncoderDriver", _FakeDriver)
    return created


@pytest.fixture(autouse=True)
def _watchdog_cleanup():
    yield
    encoder_service.stop_watchdog(join_timeout=1.0)
    encoder_service._watchdog_paused.clear()


# --- connect_port: beep handshake gate ---

def test_connect_port_success(monkeypatch):
    created = _patch_driver(monkeypatch, beep_ok=True)
    state = FakeState()
    ok, msg = encoder_service.connect_port(state, "COM5")
    assert ok is True
    assert msg == "Encoder conectado en COM5"
    assert state.encoder is created[0]
    assert state.encoder_port == "COM5"


def test_connect_port_rejects_device_that_does_not_ack_beep(monkeypatch):
    created = _patch_driver(monkeypatch, beep_ok=False)
    state = FakeState()
    ok, msg = encoder_service.connect_port(state, "COM5")
    assert ok is False
    assert msg == "COM5 no responde como encoder Locstar"
    assert state.encoder is None
    assert created[0].closed is True  # port released, not leaked


def test_connect_port_open_failure(monkeypatch):
    _patch_driver(monkeypatch, open_exc=OSError("port busy"))
    state = FakeState()
    ok, msg = encoder_service.connect_port(state, "COM5")
    assert ok is False
    assert "No se pudo abrir COM5" in msg
    assert state.encoder is None


def test_connect_port_success_resumes_paused_watchdog(monkeypatch):
    _patch_driver(monkeypatch, beep_ok=True)
    encoder_service.pause_watchdog()
    ok, _ = encoder_service.connect_port(FakeState(), "COM5")
    assert ok is True
    assert not encoder_service._watchdog_paused.is_set()


# --- auto_connect: hint-only scan restriction ---

def _fake_ports():
    return [
        ComPortInfo("COM1", "Communications Port", "Microsoft", False),
        ComPortInfo("COM7", "Silicon Labs CP210x USB to UART Bridge", "Silicon Labs", True),
    ]


def test_auto_connect_hint_only_probes_only_hint_ports(monkeypatch):
    calls = []
    monkeypatch.setattr(encoder_service, "list_com_ports", _fake_ports)
    monkeypatch.setattr(
        encoder_service, "detect_encoder_port",
        lambda ports=None: calls.append(ports) or None,
    )
    ok, _ = encoder_service.auto_connect(FakeState(), hint_only=True)
    assert ok is False
    assert calls == [["COM7"]]


def test_auto_connect_hint_only_skips_scan_when_no_hint_ports(monkeypatch):
    calls = []
    monkeypatch.setattr(
        encoder_service, "list_com_ports",
        lambda: [ComPortInfo("COM1", "Communications Port", "Microsoft", False)],
    )
    monkeypatch.setattr(
        encoder_service, "detect_encoder_port",
        lambda ports=None: calls.append(ports) or None,
    )
    ok, msg = encoder_service.auto_connect(FakeState(), hint_only=True)
    assert ok is False
    assert "No se detecto" in msg
    assert calls == []  # never touched the serial layer


def test_auto_connect_default_is_full_scan(monkeypatch):
    calls = []
    monkeypatch.setattr(
        encoder_service, "detect_encoder_port",
        lambda ports=None: calls.append(ports) or None,
    )
    ok, _ = encoder_service.auto_connect(FakeState())
    assert ok is False
    assert calls == [None]  # explicit user action scans all ports


# --- disconnect: watchdog pause flag ---

def test_disconnect_pauses_watchdog_by_default(monkeypatch):
    created = _patch_driver(monkeypatch, beep_ok=True)
    state = FakeState()
    encoder_service.connect_port(state, "COM5")
    encoder_service.disconnect(state)
    assert encoder_service._watchdog_paused.is_set()
    assert state.encoder is None
    assert created[0].closed is True


def test_disconnect_without_pause_leaves_watchdog_running(monkeypatch):
    _patch_driver(monkeypatch, beep_ok=True)
    state = FakeState()
    encoder_service.connect_port(state, "COM5")
    encoder_service.disconnect(state, pause_watchdog=False)
    assert not encoder_service._watchdog_paused.is_set()
    assert state.encoder is None


# --- watchdog tick: last-known-port preservation + hint-only rescan ---

def test_watchdog_tick_retries_last_known_port_after_failed_reconnect(monkeypatch):
    attempts = []
    monkeypatch.setattr(
        encoder_service, "connect_port",
        lambda state, port: attempts.append(port) or (False, "nope"),
    )
    state = FakeState()  # connect_port failure already cleared encoder_port
    remembered = encoder_service._watchdog_tick(state, "COM3")
    assert attempts == ["COM3"]
    assert remembered == "COM3"  # NOT lost — next tick retries the same port


def test_watchdog_tick_without_any_port_uses_hint_only_autoconnect(monkeypatch):
    calls = []
    monkeypatch.setattr(
        encoder_service, "auto_connect",
        lambda state, hint_only=False: calls.append(hint_only) or (False, "nope"),
    )
    encoder_service._watchdog_tick(FakeState(), None)
    assert calls == [True]


def test_watchdog_tick_keeps_port_when_connected():
    class _Connected:
        is_connected = True

    state = FakeState(encoder=_Connected(), encoder_port="COM9")
    assert encoder_service._watchdog_tick(state, None) == "COM9"


def test_watchdog_tick_swallows_and_logs_exceptions(monkeypatch):
    def _boom(state, port):
        raise RuntimeError("boom")

    monkeypatch.setattr(encoder_service, "connect_port", _boom)
    # Must not raise, must keep the last-known port
    assert encoder_service._watchdog_tick(FakeState(), "COM3") == "COM3"


# --- watchdog lifecycle: stop is joinable, restart works ---

def test_stop_watchdog_joins_thread_and_allows_restart():
    state = FakeState()
    encoder_service.start_watchdog(state, interval=30.0)
    first = encoder_service._watchdog_thread
    assert first is not None and first.is_alive()

    encoder_service.stop_watchdog(join_timeout=2.0)
    assert encoder_service._watchdog_thread is None
    assert not first.is_alive()  # initial 5s delay is interruptible

    # Old bug: a stale stop Event kept the new watchdog from ever starting
    encoder_service.start_watchdog(state, interval=30.0)
    second = encoder_service._watchdog_thread
    assert second is not None and second.is_alive()
    assert second is not first


def test_start_watchdog_resumes_paused_running_watchdog():
    state = FakeState()
    encoder_service.start_watchdog(state, interval=30.0)
    encoder_service.pause_watchdog()
    assert encoder_service._watchdog_paused.is_set()
    encoder_service.start_watchdog(state, interval=30.0)  # same thread, resumed
    assert not encoder_service._watchdog_paused.is_set()
