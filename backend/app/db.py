from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def create_database(database_path: Path):
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine, session_factory


def initialize_database(engine) -> None:
    # Import models before create_all so every table is registered on the metadata.
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
    # The local MVP intentionally has no migration dependency. Keep additive
    # schema changes recoverable for an existing SQLite data directory.
    if engine.dialect.name == "sqlite":
        additive_columns = {
            "runs": {
                "kind": "VARCHAR(30) DEFAULT 'engineering'",
                "stage": "VARCHAR(30) DEFAULT 'checks'",
                "specification_revision": "INTEGER",
                "generation_proposal_id": "VARCHAR(36)",
                "snapshot_id": "VARCHAR(80)",
                "snapshot_json": "JSON",
                "request_json": "JSON",
                "toolchain_json": "JSON",
                "error_code": "VARCHAR(80)",
                "error_message": "VARCHAR(500)",
                "cancel_requested": "BOOLEAN DEFAULT 0",
                "started_at": "DATETIME",
                "updated_at": "DATETIME",
            },
            "artifact_references": {
                "name": "VARCHAR(255)",
                "size_bytes": "INTEGER",
                "sha256": "VARCHAR(64)",
                "content_type": "VARCHAR(120)",
                "stage": "VARCHAR(30)",
            },
            "generation_jobs": {
                "prompt_tokens": "INTEGER",
                "completion_tokens": "INTEGER",
                "total_tokens": "INTEGER",
            },
            "specification_planner_runs": {
                "updated_at": "DATETIME",
                "accepted_revision": "INTEGER",
            },
        }
        with engine.begin() as connection:
            inspector = inspect(connection)
            for table, columns in additive_columns.items():
                existing = {item["name"] for item in inspector.get_columns(table)}
                for name, sql_type in columns.items():
                    if name not in existing:
                        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
            connection.execute(text("UPDATE runs SET kind = 'engineering' WHERE kind IS NULL"))
            connection.execute(text("UPDATE runs SET stage = 'checks' WHERE stage IS NULL"))
            connection.execute(text("UPDATE runs SET cancel_requested = 0 WHERE cancel_requested IS NULL"))
            connection.execute(text("UPDATE runs SET updated_at = created_at WHERE updated_at IS NULL"))
            if "updated_at" in additive_columns.get("specification_planner_runs", {}):
                connection.execute(
                    text("UPDATE specification_planner_runs SET updated_at = created_at WHERE updated_at IS NULL")
                )
