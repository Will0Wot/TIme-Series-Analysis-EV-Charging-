"""Streamlit dashboard for charger reliability (uptime, fault hotspots)."""

import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from src.config import DATABASE_URL  # noqa: E402


@lru_cache(maxsize=1)
def get_engine():
    return create_engine(DATABASE_URL)


def load_status_data(window_days: int = 7) -> pd.DataFrame:
    """Load charger_status for recent window."""
    engine = get_engine()
    query = text(
        """
        SELECT
            cs.time,
            cs.charger_id,
            cs.status,
            cs.session_id,
            s.site_id,
            s.name AS site_name,
            c.external_id,
            c.model,
            c.connector_type
        FROM charger_status cs
        JOIN chargers c ON cs.charger_id = c.charger_id
        JOIN sites s ON c.site_id = s.site_id
        WHERE cs.time >= now() - interval :win
    """
    )
    df = pd.read_sql(query, engine, params={"win": f"{window_days} days"})
    return df


def compute_reliability(df: pd.DataFrame, level: Literal["charger", "site"] = "charger") -> pd.DataFrame:
    """Compute uptime/fault rate per charger or site."""
    key_cols = ["charger_id", "external_id", "site_id", "site_name", "model", "connector_type"]
    if level == "site":
        key_cols = ["site_id", "site_name"]

    grouped = (
        df.groupby(key_cols)
        .agg(
            samples=("status", "count"),
            fault_samples=("status", lambda s: (s.isin(["FAULTED", "OFFLINE"])).sum()),
        )
        .reset_index()
    )
    grouped["fault_rate"] = grouped["fault_samples"] / grouped["samples"]
    grouped["uptime_pct"] = (1 - grouped["fault_rate"]) * 100
    return grouped.sort_values("fault_rate", ascending=False)


def load_charger_metadata() -> pd.DataFrame:
    engine = get_engine()
    df = pd.read_sql(
        text(
            """
            SELECT c.charger_id,
                   c.external_id,
                   c.model,
                   c.connector_type,
                   c.max_power_kw,
                   s.site_id,
                   s.name AS site_name,
                   s.city,
                   s.country
            FROM chargers c
            JOIN sites s ON c.site_id = s.site_id
            """
        ),
        engine,
    )
    return df


def main():
    st.set_page_config(page_title="Charger Reliability", layout="wide")
    st.title("Charger Reliability Dashboard")
    st.caption("Uptime and fault hotspots from charger_status (TimescaleDB)")

    days = st.sidebar.slider("Window (days)", min_value=1, max_value=30, value=7, step=1)
    df_status = load_status_data(window_days=days)
    meta = load_charger_metadata()

    if df_status.empty:
        st.warning("No status data in the selected window.")
        return

    st.subheader(f"Overview (last {days} days)")
    col1, col2, col3 = st.columns(3)
    col1.metric("Status samples", f"{len(df_status):,}")
    col2.metric("Chargers", f"{df_status['charger_id'].nunique():,}")
    col3.metric("Sites", f"{df_status['site_id'].nunique():,}")

    per_charger = compute_reliability(df_status, level="charger")
    per_site = compute_reliability(df_status, level="site")

    st.subheader("Worst chargers (by fault rate)")
    st.dataframe(
        per_charger[
            ["external_id", "site_name", "model", "connector_type", "samples", "fault_samples", "fault_rate", "uptime_pct"]
        ]
        .head(15)
        .style.format({"fault_rate": "{:.2%}", "uptime_pct": "{:.2f}"}),
        use_container_width=True,
    )

    st.subheader("Sites by reliability")
    st.dataframe(
        per_site[["site_name", "samples", "fault_samples", "fault_rate", "uptime_pct"]]
        .sort_values("fault_rate", ascending=False)
        .style.format({"fault_rate": "{:.2%}", "uptime_pct": "{:.2f}"}),
        use_container_width=True,
    )

    st.subheader("Faults by model / connector")
    pivot_model = (
        per_charger.groupby(["model", "connector_type"]).agg(
            chargers=("external_id", "count"),
            avg_fault_rate=("fault_rate", "mean"),
            avg_uptime=("uptime_pct", "mean"),
        )
    ).reset_index()
    st.dataframe(
        pivot_model.sort_values("avg_fault_rate", ascending=False).style.format({"avg_fault_rate": "{:.2%}", "avg_uptime": "{:.2f}"}),
        use_container_width=True,
    )

    st.markdown("###### Raw status sample (head)")
    st.dataframe(df_status.sort_values("time", ascending=False).head(50), use_container_width=True)


if __name__ == "__main__":
    main()
