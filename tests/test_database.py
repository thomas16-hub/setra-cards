"""Tests de configuración de la base de datos."""
from sqlalchemy import create_engine, event, text
from setra_cards.storage.models import Base


def test_foreign_keys_pragma():
    engine = create_engine("sqlite:///:memory:", future=True)

    @event.listens_for(engine, "connect")
    def set_fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA foreign_keys")).fetchone()
        assert result[0] == 1, "PRAGMA foreign_keys debe estar ON"
