"""Integration test: seed a temporary TimescaleDB and verify schema/data."""
from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url

from src.config import DATABASE_URL
from scripts import generate_ev_data as gen


def _admin_engine():
    url = make_url(DATABASE_URL)
    return create_engine(url.set(database="postgres"))


def _drop_db(admin_engine, db_name: str) -> None:
    with admin_engine.connect() as conn:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))


@pytest.mark.integration
def test_timescale_schema_and_data_smoke():
    base_url = make_url(DATABASE_URL)
    temp_db = f"{base_url.database}_test_{uuid.uuid4().hex[:8]}"
    admin_engine = _admin_engine()

    _drop_db(admin_engine, temp_db)
    with admin_engine.connect() as conn:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text(f"CREATE DATABASE {temp_db} TEMPLATE template0"))

    temp_url = base_url.set(database=temp_db)
    engine = create_engine(temp_url)

    try:
        # Schema + seed minimal data
        gen.init_schema(engine)
        sites = gen.seed_sites(engine)
        chargers = gen.seed_chargers(engine, gen.build_chargers(sites[:2], total=3))

        window_end = datetime.now(timezone.utc)
        window_start = window_end - timedelta(days=1)

        all_sessions = []
        for charger in chargers:
            all_sessions.extend(gen.generate_charging_sessions(charger, window_start, window_end))
        gen.insert_charging_sessions(engine, all_sessions)

        for charger in chargers:
            events = gen.generate_status_events(charger, [], window_start, window_end)
            gen.insert_status_events(engine, events)

        with engine.connect() as conn:
            # Counts
            counts = {
                tbl: conn.execute(text(f"SELECT count(*) FROM {tbl}")).scalar()
                for tbl in ["sites", "chargers", "charging_sessions", "charger_status"]
            }
            assert counts["sites"] > 0
            assert counts["chargers"] > 0
            assert counts["charging_sessions"] > 0
            assert counts["charger_status"] > 0

            # Hypertables exist
            hypertables = conn.execute(
                text(
                    """
                    SELECT hypertable_name
                    FROM timescaledb_information.hypertables
                    WHERE hypertable_name IN ('charging_sessions', 'charger_status')
                    """
                )
            ).scalars().all()
            assert set(hypertables) == {"charging_sessions", "charger_status"}

            # Indexes exist
            expected_indexes = {
                "idx_chargers_site",
                "idx_charger_status_charger",
                "idx_sessions_charger",
                "idx_sessions_site",
                "idx_sessions_session_id_start_time",
            }
            existing_indexes = set(
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT indexname
                        FROM pg_indexes
                        WHERE tablename IN ('chargers','charger_status','charging_sessions')
                        """
                    )
                )
            )
            missing = expected_indexes - existing_indexes
            assert not missing, f"Missing indexes: {missing}"
    finally:
        engine.dispose()
        _drop_db(admin_engine, temp_db)
        admin_engine.dispose()
