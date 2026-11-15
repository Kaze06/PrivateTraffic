from sqlalchemy import create_engine, text, inspect
from app.database import initialize_schema
from app import models


def test_additive_migration_preserves_legacy_records(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE camera_sources (id VARCHAR PRIMARY KEY, title VARCHAR)"))
        connection.execute(text("INSERT INTO camera_sources VALUES ('existing', 'Original road')"))
        connection.execute(text("CREATE TABLE traffic_measurements (id INTEGER PRIMARY KEY, vehicle_count INTEGER)"))
        connection.execute(text("INSERT INTO traffic_measurements VALUES (7, 12)"))
        connection.execute(text("CREATE TABLE alert_rules (id INTEGER PRIMARY KEY, enabled BOOLEAN)"))
        connection.execute(text("INSERT INTO alert_rules VALUES (3, 1)"))
        connection.execute(text("CREATE TABLE alert_events (id INTEGER PRIMARY KEY, reason VARCHAR)"))
        connection.execute(text("INSERT INTO alert_events VALUES (5, 'original event')"))
    initialize_schema(engine)
    initialize_schema(engine)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT title, calibration_t1, original_stream_url FROM camera_sources")).one() == ('Original road', None, None)
        assert connection.execute(text("SELECT id,vehicle_count,window_seconds,validity FROM traffic_measurements")).one() == (7,12,60,'valid')
        assert connection.execute(text("SELECT id,session_id,was_above FROM alert_rules")).one() == (3,None,None)
        assert connection.execute(text("SELECT reason,measurement_id FROM alert_events")).one() == ('original event',None)
    assert 'ix_alert_rules_session_id' in {i['name'] for i in inspect(engine).get_indexes('alert_rules')}
    engine.dispose()
