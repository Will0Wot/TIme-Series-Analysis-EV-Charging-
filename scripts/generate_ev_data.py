"""Generate synthetic EV charging data and load it into TimescaleDB."""

import argparse
from datetime import datetime, timedelta, timezone
import random
import sys
from pathlib import Path
import uuid
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import DATABASE_URL  # noqa: E402


SITE_SEED: Sequence[Dict[str, object]] = [
    {
        "name": "Downtown Hub",
        "city": "San Francisco",
        "country": "USA",
        "timezone": "America/Los_Angeles",
        "latitude": 37.7749,
        "longitude": -122.4194,
    },
    {
        "name": "Tech Park",
        "city": "Seattle",
        "country": "USA",
        "timezone": "America/Los_Angeles",
        "latitude": 47.6062,
        "longitude": -122.3321,
    },
    {
        "name": "Airport Plaza",
        "city": "Denver",
        "country": "USA",
        "timezone": "America/Denver",
        "latitude": 39.7392,
        "longitude": -104.9903,
    },
    {
        "name": "Harbor Station",
        "city": "Boston",
        "country": "USA",
        "timezone": "America/New_York",
        "latitude": 42.3601,
        "longitude": -71.0589,
    },
    {
        "name": "Central Mall",
        "city": "Chicago",
        "country": "USA",
        "timezone": "America/Chicago",
        "latitude": 41.8781,
        "longitude": -87.6298,
    },
    {
        "name": "University Quad",
        "city": "Austin",
        "country": "USA",
        "timezone": "America/Chicago",
        "latitude": 30.2672,
        "longitude": -97.7431,
    },
    {
        "name": "Financial District",
        "city": "New York",
        "country": "USA",
        "timezone": "America/New_York",
        "latitude": 40.7128,
        "longitude": -74.0060,
    },
    {
        "name": "Lakeside",
        "city": "Toronto",
        "country": "Canada",
        "timezone": "America/Toronto",
        "latitude": 43.6532,
        "longitude": -79.3832,
    },
    {
        "name": "Innovation Center",
        "city": "Vancouver",
        "country": "Canada",
        "timezone": "America/Vancouver",
        "latitude": 49.2827,
        "longitude": -123.1207,
    },
    {
        "name": "Transit Hub",
        "city": "Montreal",
        "country": "Canada",
        "timezone": "America/Toronto",
        "latitude": 45.5017,
        "longitude": -73.5673,
    },
]

CONNECTOR_TYPES = ["CCS", "CHAdeMO", "NACS", "Type2"]
CHARGER_MODELS = ["Delta-50", "ABB-Terra", "ChargePoint-Express", "EVgo-Fast"]


def init_schema(engine) -> None:
    schema_sql = """
    CREATE EXTENSION IF NOT EXISTS timescaledb;

    CREATE TABLE IF NOT EXISTS sites (
        site_id SERIAL PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        city TEXT NOT NULL,
        country TEXT NOT NULL,
        timezone TEXT NOT NULL,
        latitude DOUBLE PRECISION,
        longitude DOUBLE PRECISION
    );

    CREATE TABLE IF NOT EXISTS chargers (
        charger_id SERIAL PRIMARY KEY,
        site_id INTEGER NOT NULL REFERENCES sites(site_id),
        external_id TEXT NOT NULL UNIQUE,
        model TEXT,
        max_power_kw NUMERIC(6, 2) NOT NULL,
        connector_type TEXT NOT NULL,
        installed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_chargers_site ON chargers (site_id);

    CREATE TABLE IF NOT EXISTS charger_status (
        time TIMESTAMPTZ NOT NULL,
        charger_id INTEGER NOT NULL REFERENCES chargers(charger_id),
        status TEXT NOT NULL,
        error_code TEXT,
        temperature_celsius NUMERIC(6, 2),
        session_id UUID,
        PRIMARY KEY (time, charger_id)
    );
    SELECT create_hypertable('charger_status', 'time', if_not_exists => true, chunk_time_interval => interval '3 days');
    CREATE INDEX IF NOT EXISTS idx_charger_status_charger ON charger_status (charger_id, time DESC);

    CREATE TABLE IF NOT EXISTS charging_sessions (
        session_id UUID NOT NULL,
        charger_id INTEGER NOT NULL REFERENCES chargers(charger_id),
        site_id INTEGER NOT NULL REFERENCES sites(site_id),
        vehicle_id TEXT,
        start_time TIMESTAMPTZ NOT NULL,
        end_time TIMESTAMPTZ NOT NULL,
        duration_minutes INTEGER NOT NULL,
        energy_kwh NUMERIC(10, 2) NOT NULL,
        avg_power_kw NUMERIC(8, 2),
        max_power_kw NUMERIC(8, 2),
        success BOOLEAN NOT NULL DEFAULT TRUE,
        stop_reason TEXT
    );
    ALTER TABLE charging_sessions DROP CONSTRAINT IF EXISTS charging_sessions_pkey;
    ALTER TABLE charging_sessions ADD PRIMARY KEY (start_time, session_id);
    DROP INDEX IF EXISTS idx_sessions_session_id;
    CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_session_id_start_time ON charging_sessions (session_id, start_time);
    SELECT create_hypertable(
        'charging_sessions',
        'start_time',
        if_not_exists => true,
        migrate_data => true,
        chunk_time_interval => interval '7 days'
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_charger ON charging_sessions (charger_id, start_time DESC);
    CREATE INDEX IF NOT EXISTS idx_sessions_site ON charging_sessions (site_id, start_time DESC);
    """
    with engine.begin() as conn:
        conn.execute(text(schema_sql))


def seed_sites(engine) -> List[Dict[str, object]]:
    seeded_sites: List[Dict[str, object]] = []
    with engine.begin() as conn:
        for site in SITE_SEED:
            result = conn.execute(
                text(
                    """
                    INSERT INTO sites (name, city, country, timezone, latitude, longitude)
                    VALUES (:name, :city, :country, :timezone, :latitude, :longitude)
                    ON CONFLICT (name) DO UPDATE SET
                        city = EXCLUDED.city,
                        country = EXCLUDED.country,
                        timezone = EXCLUDED.timezone,
                        latitude = EXCLUDED.latitude,
                        longitude = EXCLUDED.longitude
                    RETURNING site_id;
                    """
                ),
                site,
            )
            site_with_id = dict(site)
            site_with_id["site_id"] = result.scalar_one()
            seeded_sites.append(site_with_id)
    return seeded_sites


def pick_sites(site_seed: Sequence[Dict[str, object]], desired_count: int) -> List[Dict[str, object]]:
    """Return up to desired_count site definitions, repeating with suffixes if more are requested."""
    if desired_count <= len(site_seed):
        return list(site_seed)[:desired_count]

    picked = list(site_seed)
    # If more sites requested than the seed, clone entries with unique names.
    for idx in range(desired_count - len(site_seed)):
        base = random.choice(site_seed)
        clone = dict(base)
        clone["name"] = f"{base['name']} #{idx + 1}"
        picked.append(clone)
    return picked


def build_chargers(sites: Sequence[Dict[str, object]], total: int = 50) -> List[Dict[str, object]]:
    chargers: List[Dict[str, object]] = []
    install_window_days = 365 * 2
    now = datetime.now(timezone.utc)

    for idx in range(total):
        site = random.choice(sites)
        power = random.choice([50, 75, 120, 150, 250])
        chargers.append(
            {
                "site_id": site["site_id"],
                "site_name": site["name"],
                "external_id": f"CHR-{idx + 1:04d}",
                "model": random.choice(CHARGER_MODELS),
                "max_power_kw": float(power),
                "connector_type": random.choice(CONNECTOR_TYPES),
                "installed_at": now - timedelta(days=random.randint(30, install_window_days)),
            }
        )
    return chargers


def seed_chargers(engine, chargers: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    seeded = []
    with engine.begin() as conn:
        for charger in chargers:
            result = conn.execute(
                text(
                    """
                    INSERT INTO chargers (site_id, external_id, model, max_power_kw, connector_type, installed_at)
                    VALUES (:site_id, :external_id, :model, :max_power_kw, :connector_type, :installed_at)
                    ON CONFLICT (external_id) DO UPDATE SET
                        site_id = EXCLUDED.site_id,
                        max_power_kw = EXCLUDED.max_power_kw,
                        connector_type = EXCLUDED.connector_type,
                        model = EXCLUDED.model
                    RETURNING charger_id;
                    """
                ),
                charger,
            )
            charger_with_id = dict(charger)
            charger_with_id["charger_id"] = result.scalar_one()
            seeded.append(charger_with_id)
    return seeded


def generate_outage_windows(start: datetime, end: datetime) -> List[Tuple[datetime, datetime, str]]:
    windows: List[Tuple[datetime, datetime, str]] = []
    outage_count = random.randint(1, 4)
    total_span_minutes = int((end - start).total_seconds() / 60)

    for _ in range(outage_count):
        offset_minutes = random.randint(0, max(total_span_minutes - 120, 1))
        window_start = start + timedelta(minutes=offset_minutes)
        window_end = window_start + timedelta(minutes=random.randint(10, 90))
        if window_end > end:
            window_end = end
        windows.append((window_start, window_end, random.choice(["FAULTED", "OFFLINE"])))

    windows.sort(key=lambda w: w[0])
    return windows


def generate_charging_sessions(
    charger: Dict[str, object], start: datetime, end: datetime
) -> List[Dict[str, object]]:
    sessions: List[Dict[str, object]] = []
    days = (end - start).days

    for day_offset in range(days):
        day_start = start + timedelta(days=day_offset)
        session_count = random.randint(1, 4)

        for _ in range(session_count):
            session_start = day_start + timedelta(minutes=random.randint(0, (24 * 60) - 90))
            duration_minutes = random.randint(10, 90)
            session_end = session_start + timedelta(minutes=duration_minutes)
            if session_end >= end:
                continue

            energy_kwh = round(
                (duration_minutes / 60) * float(charger["max_power_kw"]) * random.uniform(0.55, 0.95), 2
            )
            avg_power_kw = round(energy_kwh / (duration_minutes / 60), 2)
            success = random.random() > 0.08
            stop_reason: Optional[str] = None
            if not success:
                stop_reason = random.choice(["fault", "user_unplug", "timeout"])

            sessions.append(
                {
                    "session_id": uuid.uuid4(),
                    "charger_id": charger["charger_id"],
                    "site_id": charger["site_id"],
                    "vehicle_id": f"VEH-{random.randint(10000, 99999)}",
                    "start_time": session_start,
                    "end_time": session_end,
                    "duration_minutes": duration_minutes,
                    "energy_kwh": energy_kwh,
                    "avg_power_kw": avg_power_kw,
                    "max_power_kw": charger["max_power_kw"],
                    "success": success,
                    "stop_reason": stop_reason,
                }
            )

    sessions.sort(key=lambda s: s["start_time"])
    return sessions


def generate_status_events(
    charger: Dict[str, object],
    sessions: Sequence[Dict[str, object]],
    start: datetime,
    end: datetime,
) -> List[Dict[str, object]]:
    events: List[Dict[str, object]] = []
    outage_windows = generate_outage_windows(start, end)
    session_index = 0
    current_time = start

    def active_outage(ts: datetime) -> Optional[str]:
        for window_start, window_end, status in outage_windows:
            if window_start <= ts <= window_end:
                return status
        return None

    while current_time <= end:
        current_session: Optional[Dict[str, object]] = None
        while session_index < len(sessions) and sessions[session_index]["end_time"] < current_time:
            session_index += 1

        if session_index < len(sessions):
            maybe_session = sessions[session_index]
            if maybe_session["start_time"] <= current_time <= maybe_session["end_time"]:
                current_session = maybe_session

        outage_status = active_outage(current_time)
        if outage_status:
            status = outage_status
            session_id = None
        elif current_session:
            status = "CHARGING"
            session_id = current_session["session_id"]
        else:
            status = random.choices(["AVAILABLE", "CHARGING"], weights=[0.6, 0.4])[0]
            session_id = None

        error_code = None
        if status == "FAULTED":
            error_code = random.choice(["OVERCURRENT", "GROUND_FAULT", "PILOT_FAILURE", "COMM_LOSS"])

        temperature = round(random.normalvariate(24, 5), 2)
        events.append(
            {
                "time": current_time,
                "charger_id": charger["charger_id"],
                "status": status,
                "error_code": error_code,
                "temperature_celsius": temperature,
                "session_id": session_id,
            }
        )

        current_time += timedelta(minutes=random.randint(1, 5))

    return events


def chunked(seq: Sequence[Dict[str, object]], size: int) -> Iterable[Sequence[Dict[str, object]]]:
    for idx in range(0, len(seq), size):
        yield seq[idx : idx + size]


def insert_charging_sessions(engine, sessions: Sequence[Dict[str, object]]) -> int:
    insert_sql = text(
        """
        INSERT INTO charging_sessions (
            session_id,
            charger_id,
            site_id,
            vehicle_id,
            start_time,
            end_time,
            duration_minutes,
            energy_kwh,
            avg_power_kw,
            max_power_kw,
            success,
            stop_reason
        ) VALUES (
            :session_id,
            :charger_id,
            :site_id,
            :vehicle_id,
            :start_time,
            :end_time,
            :duration_minutes,
            :energy_kwh,
            :avg_power_kw,
            :max_power_kw,
            :success,
            :stop_reason
        )
        ON CONFLICT (start_time, session_id) DO NOTHING;
        """
    )

    inserted = 0
    with engine.begin() as conn:
        for batch in chunked(sessions, 1000):
            conn.execute(insert_sql, batch)
            inserted += len(batch)
    return inserted


def insert_status_events(engine, events: Sequence[Dict[str, object]]) -> int:
    insert_sql = text(
        """
        INSERT INTO charger_status (
            time,
            charger_id,
            status,
            error_code,
            temperature_celsius,
            session_id
        ) VALUES (
            :time,
            :charger_id,
            :status,
            :error_code,
            :temperature_celsius,
            :session_id
        )
        ON CONFLICT DO NOTHING;
        """
    )

    inserted = 0
    with engine.begin() as conn:
        for batch in chunked(events, 5000):
            conn.execute(insert_sql, batch)
            inserted += len(batch)
    return inserted


def main() -> None:
    args = parse_args()
    random.seed(args.random_seed)
    engine = create_engine(DATABASE_URL)
    init_schema(engine)

    print("Seeding sites...")
    chosen_sites = pick_sites(SITE_SEED, args.sites)
    # Reuse the seed_sites helper for persistence
    sites = seed_sites(engine)
    # If more sites were requested than the seed list, persist the extras
    if len(chosen_sites) > len(sites):
        extra_sites = [s for s in chosen_sites if s["name"] not in {site["name"] for site in sites}]
        if extra_sites:
            with engine.begin() as conn:
                for site in extra_sites:
                    result = conn.execute(
                        text(
                            """
                            INSERT INTO sites (name, city, country, timezone, latitude, longitude)
                            VALUES (:name, :city, :country, :timezone, :latitude, :longitude)
                            ON CONFLICT (name) DO NOTHING
                            RETURNING site_id;
                            """
                        ),
                        site,
                    )
                    site_id = result.scalar()
                    if site_id:
                        site_with_id = dict(site)
                        site_with_id["site_id"] = site_id
                        sites.append(site_with_id)
    print(f"Seeded {len(sites)} sites")

    print("Seeding chargers...")
    chargers = seed_chargers(engine, build_chargers(sites, total=args.chargers))
    print(f"Seeded {len(chargers)} chargers")

    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=args.days)

    all_sessions: List[Dict[str, object]] = []
    sessions_by_charger: Dict[int, List[Dict[str, object]]] = {}

    print("Generating sessions...")
    for charger in chargers:
        charger_sessions = generate_charging_sessions(charger, window_start, window_end)
        sessions_by_charger[charger["charger_id"]] = charger_sessions
        all_sessions.extend(charger_sessions)

    print("Inserting charging sessions...")
    session_count = insert_charging_sessions(engine, all_sessions)
    print(f"Inserted {session_count} charging sessions")

    print("Generating and inserting charger status events...")
    status_count = 0
    for charger in chargers:
        events = generate_status_events(
            charger, sessions_by_charger.get(charger["charger_id"], []), window_start, window_end
        )
        status_count += insert_status_events(engine, events)

    print(f"Inserted {status_count} charger status rows")

    engine.dispose()
    print("Data generation complete.")


def parse_args() -> argparse.Namespace:
    """CLI options so you can scale data volume for demos/load-testing."""
    parser = argparse.ArgumentParser(description="Generate synthetic EV charging data into TimescaleDB.")
    parser.add_argument("--days", type=int, default=30, help="Number of days of history to generate (default: 30)")
    parser.add_argument("--sites", type=int, default=len(SITE_SEED), help="How many sites to seed (default: 10)")
    parser.add_argument("--chargers", type=int, default=50, help="How many chargers to seed (default: 50)")
    parser.add_argument("--random-seed", type=int, default=42, help="Random seed for reproducibility")
    return parser.parse_args()


if __name__ == "__main__":
    main()
