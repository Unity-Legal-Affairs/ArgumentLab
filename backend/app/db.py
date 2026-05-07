from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
connect_args = {"check_same_thread": False} if settings.effective_database_url.startswith("sqlite") else {}
engine = create_engine(settings.effective_database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    run_lightweight_migrations()


def run_lightweight_migrations() -> None:
    """Apply additive local SQLite/Postgres-safe columns for prototype upgrades.

    This is intentionally narrow. v0.1 does not ship Alembic yet, but existing
    local users should not have to delete `~/.argument-lab` when we add
    trust/traceability metadata.
    """

    inspector = inspect(engine)
    if not inspector.has_table("simulation_turns"):
        return
    columns = {column["name"] for column in inspector.get_columns("simulation_turns")}
    additions = {
        "model_requested": "VARCHAR(300)",
        "model_used": "VARCHAR(300)",
        "provider_status": "VARCHAR(64) DEFAULT 'unknown' NOT NULL",
        "schema_validated": "BOOLEAN DEFAULT 0 NOT NULL",
        "error": "TEXT",
    }
    with engine.begin() as connection:
        for name, ddl_type in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE simulation_turns ADD COLUMN {name} {ddl_type}"))
