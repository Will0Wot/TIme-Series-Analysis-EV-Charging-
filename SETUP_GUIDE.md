# EV Charging Time Series Setup Guide

## Environment Setup

The Python virtual environment has been automatically configured with all required dependencies.

### Verify Installation

```bash
# Check Python version
python --version

# Verify installed packages
pip list | grep -E "pandas|numpy|timescaledb|fastapi"
```

### Core Packages Installed

- **Database**: psycopg2, SQLAlchemy, timescaledb-python
- **Data**: pandas, numpy, scipy, scikit-learn
- **Visualization**: matplotlib, seaborn, plotly
- **Forecasting**: statsmodels, prophet, xgboost, lightgbm, tensorflow
- **API**: FastAPI, uvicorn, Flask
- **Development**: pytest, black, flake8, mypy
- **Notebooks**: Jupyter, JupyterLab

## Database Setup

### Option 1: Docker (Recommended)

```bash
# Start TimescaleDB (maps container 5432 to host 5433)
docker-compose up -d

# Verify running
docker ps | grep timescaledb
```

### Option 2: Local Installation

```bash
# Create database
createdb -U postgres ev_charging

# Enable TimescaleDB
psql -U postgres -d ev_charging -c "CREATE EXTENSION timescaledb"
```

## Configuration

Create `.env` file from template:

```bash
cp .env.example .env
```

Edit `.env` with your settings:
```
TIMESCALEDB_HOST=127.0.0.1
TIMESCALEDB_PORT=5433
TIMESCALEDB_USER=postgres
TIMESCALEDB_PASSWORD=postgres
TIMESCALEDB_DATABASE=ev_charging
```

## Run Services

### API Server
```bash
python -m uvicorn src.api.main:app --reload
# Access: http://localhost:8000
# Docs: http://localhost:8000/docs
```

### Jupyter Notebook
```bash
jupyter lab --notebook-dir=notebooks
```

### Tests
```bash
pytest tests/ -m "not integration" -v
pytest tests/test_db_integration.py -m integration -v
```

### Data Generator
```bash
# Default: 30 days, 10 sites, 50 chargers
python scripts/generate_ev_data.py

# Scale volume
python scripts/generate_ev_data.py --days 7 --sites 5 --chargers 12 --random-seed 123
```

## VS Code Integration

The `.vscode/` folder contains:
- `settings.json`: Python interpreter and linting config
- `tasks.json`: Pre-configured tasks

Access tasks with: `Ctrl+Shift+P` → "Tasks: Run Task"

Available tasks:
- Start TimescaleDB
- Run API Server
- Run Tests

## File Structure

```
Project TIme/
├── src/
│   ├── config.py           # Configuration
│   ├── api/
│   │   ├── main.py        # FastAPI app
│   │   └── routes.py      # API endpoints
│   ├── database/
│   │   └── timescale.py   # DB connections
│   ├── models/
│   │   ├── arima.py       # ARIMA model
│   │   └── prophet.py     # Prophet model
│   ├── analysis/
│   │   └── statistics.py  # Analysis tools
│   └── utils/
│       └── data_loader.py # Data utilities
├── notebooks/
│   ├── 01_data_exploration.ipynb
│   └── Untitled.ipynb
├── tests/
│   ├── test_data_loader.py
│   ├── test_db_integration.py
│   └── conftest.py
├── scripts/
│   └── generate_ev_data.py
├── data/
│   ├── raw/
│   └── processed/
├── .env.example
├── .gitignore
├── docker-compose.yml
├── requirements.txt
├── pyproject.toml
├── README.md
└── SETUP_GUIDE.md
```

## Troubleshooting

### Import Errors
- Verify virtual environment: `which python`
- Reinstall packages: `pip install -r requirements.txt`

### Database Connection
- Check TimescaleDB running: `docker ps`
- Verify credentials in `.env`
- Test connection: `psql -h 127.0.0.1 -p 5433 -U postgres -d ev_charging`

### Port Already in Use
- Change API_PORT in `.env`
- Or kill process: `lsof -i :8000`

## Next Steps

1. Review project structure
2. Start TimescaleDB
3. Initialize database
4. Explore notebooks
5. Run API server
6. Add your data to `data/raw/`

Happy analyzing!
