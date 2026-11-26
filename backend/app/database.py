from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import DATABASE_PATH

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

# check_same_thread=False allows FastAPI background threads to access SQLite
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def initialize_schema(bind=engine):
    """Add first-release columns without replacing existing SQLite records."""
    Base.metadata.create_all(bind=bind)
    additions = {
        "camera_sources": {
            "calibration_t1": "FLOAT", "calibration_t2": "FLOAT",
            "calibration_note": "VARCHAR", "recording_time": "DATETIME",
            "permission_reference": "VARCHAR", "original_stream_url": "VARCHAR",
        },
        "traffic_measurements": {
            "window_seconds": "FLOAT NOT NULL DEFAULT 60",
            "validity": "VARCHAR NOT NULL DEFAULT 'valid'",
            "no_measurement_reason": "VARCHAR",
            "timestamp_basis": "VARCHAR NOT NULL DEFAULT 'source_observation'",
        },
        "alert_rules": {
            "session_id": "VARCHAR", "last_seen_at": "DATETIME",
            "was_above": "BOOLEAN",
        },
        "alert_events": {"measurement_id": "INTEGER"},
    }
    with bind.begin() as connection:
        inspector = inspect(connection)
        for table, columns in additions.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}'))
        connection.execute(text('CREATE INDEX IF NOT EXISTS ix_alert_rules_session_id ON alert_rules (session_id)'))
