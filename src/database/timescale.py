"""Database connection and management for TimescaleDB."""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from typing import Optional, List, Dict, Any
import pandas as pd
from src.config import DATABASE_URL


class TimescaleDBConnection:
    """Manages TimescaleDB connections and operations."""

    def __init__(self, database_url: str = DATABASE_URL):
        """Initialize database connection with tuned pooling."""
        self.engine = create_engine(
            database_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_use_lifo=True,
        )
        self.SessionLocal = sessionmaker(bind=self.engine)

    def get_session(self) -> Session:
        """Get a new database session."""
        return self.SessionLocal()

    def create_hypertables(self) -> None:
        """Create hypertables for time series data."""
        session = self.get_session()
        try:
            # Create charging_sessions hypertable
            session.execute(text("""
                CREATE TABLE IF NOT EXISTS charging_sessions (
                    id SERIAL,
                    time TIMESTAMPTZ NOT NULL,
                    session_id TEXT NOT NULL,
                    vehicle_id TEXT NOT NULL,
                    charger_id TEXT NOT NULL,
                    power_kw FLOAT NOT NULL,
                    energy_delivered_kwh FLOAT NOT NULL,
                    temperature_celsius FLOAT,
                    PRIMARY KEY (time, id)
                );
                
                SELECT create_hypertable('charging_sessions', 'time', if_not_exists => true);
                CREATE INDEX IF NOT EXISTS idx_charging_sessions_vehicle ON charging_sessions (vehicle_id, time DESC);
                CREATE INDEX IF NOT EXISTS idx_charging_sessions_charger ON charging_sessions (charger_id, time DESC);
            """))
            
            session.commit()
            print("Hypertables created successfully!")
        except Exception as e:
            print(f"Error creating hypertables: {e}")
            session.rollback()
        finally:
            session.close()

    def close(self) -> None:
        """Close database connection."""
        self.engine.dispose()


# Global database instance
db = TimescaleDBConnection()
