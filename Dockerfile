# Python + FastAPI container for the EV charging reliability project
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps for psycopg2
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Expose API port (FastAPI). For Streamlit, override command/port at runtime.
EXPOSE 8000

# Default: run FastAPI. For Streamlit, override with:
# CMD ["streamlit", "run", "scripts/streamlit_dashboard.py", "--server.address=0.0.0.0", "--server.port=8501"]
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
