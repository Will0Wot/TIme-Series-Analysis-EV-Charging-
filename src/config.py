"""Configuration management for EV charging analysis project."""
import os
from dotenv import load_dotenv

load_dotenv()


def _parse_port(value: str, default: int) -> int:
    """Parse port value that might be provided as 'host:port' or just 'port'."""
    if not value:
        return default
    # If format like "5432:5432", take the first numeric component.
    part = str(value).split(":")[0]
    try:
        return int(part)
    except (TypeError, ValueError):
        return default

# Database Configuration
TIMESCALEDB_HOST = os.getenv("TIMESCALEDB_HOST", "127.0.0.1")
TIMESCALEDB_PORT = _parse_port(os.getenv("TIMESCALEDB_PORT", "5433"), 5433)
TIMESCALEDB_USER = os.getenv("TIMESCALEDB_USER", "postgres")
TIMESCALEDB_PASSWORD = os.getenv("TIMESCALEDB_PASSWORD", "postgres")
TIMESCALEDB_DATABASE = os.getenv("TIMESCALEDB_DATABASE", "ev_charging")

# Prefer DATABASE_URL env var (used by Streamlit secrets) if provided
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql://{TIMESCALEDB_USER}:{TIMESCALEDB_PASSWORD}@{TIMESCALEDB_HOST}:{TIMESCALEDB_PORT}/{TIMESCALEDB_DATABASE}",
)

# API Configuration
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", 8000))
DEBUG = os.getenv("DEBUG", "True").lower() == "true"

# Data Configuration
DATA_PATH = os.getenv("DATA_PATH", "./data")
MODELS_PATH = os.getenv("MODELS_PATH", "./models")

# Create directories if they don't exist
os.makedirs(DATA_PATH, exist_ok=True)
os.makedirs(MODELS_PATH, exist_ok=True)

# Model Configuration
FORECAST_HORIZON = 24  # hours
LOOKBACK_WINDOW = 7 * 24  # 7 days in hours
TEST_SIZE = 0.2
VALIDATION_SIZE = 0.1
