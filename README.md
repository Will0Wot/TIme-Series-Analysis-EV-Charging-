# EV Charging Reliability Demo (TimescaleDB + FastAPI + Streamlit)

End-to-end passion project for EV charger reliability: synthetic status pings and sessions into TimescaleDB, FastAPI reliability endpoints, a Streamlit dashboard, and notebooks for deeper analysis.

Use it to spin up a full local stack that streams charger heartbeats and sessions into Timescale, inspect reliability KPIs via the FastAPI endpoints or Streamlit dashboard, and run experiments such as fault-rate sensitivity to load, uptime impacts from simulated outages, anomaly/alert tuning, predictive maintenance baselines (Prophet/XGBoost/LSTM stubs), and capacity planning “what-ifs” across sites and charger models.

## Quick Start
```bash
# 0) Prereqs: Python 3.13+, Docker (for Timescale)
python --version

# 1) Activate env and copy config
source .venv/bin/activate
cp .env.example .env    # defaults: host 127.0.0.1, port 5433, postgres/postgres

# 2) Start TimescaleDB (host 127.0.0.1:5433)
docker-compose up -d

# 3) Seed synthetic data (defaults: 30 days, 10 sites, 50 chargers)
python scripts/generate_ev_data.py
# Scale volume if needed:
# python scripts/generate_ev_data.py --days 7 --sites 5 --chargers 12 --random-seed 123

# 4) Run API (FastAPI)
# Prod-lean (uvloop + multiple workers):
uvicorn src.api.main:app --loop uvloop --http httptools --workers 4
# Dev hot-reload:
python -m uvicorn src.api.main:app --reload
# Docs: http://localhost:8000/docs
# Key endpoints: /api/chargers, /api/chargers/{id}/stats, /api/alerts, /api/reliability?scope=site|model&days=N

# 5) Reliability dashboard (Streamlit)
streamlit run scripts/streamlit_dashboard.py

# 6) Explore in notebooks
jupyter lab --notebook-dir=notebooks

# 7) Tests
pytest tests/ -m "not integration" -v
pytest tests/test_db_integration.py -m integration -v
```

## What you can do with it
- Demo reliability KPIs (uptime, fault/offline rate, MTBF/MTTR) and active alerts across chargers/sites.
- Identify offender chargers/models/connectors and lost session minutes (user impact).
- Explore load vs faults in notebooks; prototype dashboards via Streamlit or the FastAPI JSON APIs.

## Features
- **TimescaleDB hypertables**: `charger_status` (1–5 min pings) and `charging_sessions` (session metadata) with indexes for charger/site queries.
- **Synthetic generator**: realistic sites/chargers, outages, sessions; CLI flags for days/sites/chargers.
- **Reliability API**: chargers list, charger stats, active alerts (FAULTED/OFFLINE windows), uptime/MTBF/MTTR by site/model.
- **Streamlit dashboard**: worst chargers/sites, model/connector hotspots, adjustable window; runs locally with `streamlit run scripts/streamlit_dashboard.py`.
- **Notebooks**: utilization vs fault trends, offenders by fault rate and lost session minutes, load/fault correlation, data story summary.
- **Tests**: integration test seeds a temp TimescaleDB, checks hypertables/indexes and data counts.

## Tech Stack
- DB: TimescaleDB/PostgreSQL
- API: FastAPI, Pydantic, SQLAlchemy, psycopg2
- Data/Vis: Pandas, Plotly, Streamlit, Jupyter
- Infra: Docker Compose
- Quality: Pytest, Black, Flake8, Mypy

## Notes
- Default DB host/port: `127.0.0.1:5433` (docker-compose maps container 5432 to host 5433).
- Update `.env` if you point to an existing Postgres/Timescale instance.
- For Git pushes, ensure you pull/rebase against origin/main and push with your GitHub credentials/PAT.
