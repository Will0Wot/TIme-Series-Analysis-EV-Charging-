"""Streamlit dashboard for charger reliability (uptime, fault hotspots)."""

import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

import pandas as pd
import streamlit as st
import plotly.express as px
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


def load_session_data(window_days: int = 30) -> pd.DataFrame:
    """Load charging_sessions for recent window."""
    engine = get_engine()
    query = text(
        """
        SELECT
            cs.session_id,
            cs.charger_id,
            cs.site_id,
            cs.start_time,
            cs.end_time,
            cs.duration_minutes,
            cs.energy_kwh,
            cs.success,
            cs.stop_reason,
            c.external_id,
            c.model,
            c.connector_type,
            s.name AS site_name
        FROM charging_sessions cs
        JOIN chargers c ON cs.charger_id = c.charger_id
        JOIN sites s ON cs.site_id = s.site_id
        WHERE cs.start_time >= now() - interval :win
    """
    )
    return pd.read_sql(query, engine, params={"win": f"{window_days} days"})


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

    status_window = st.sidebar.slider("Status window (days)", min_value=1, max_value=30, value=7, step=1)
    session_window = st.sidebar.slider("Sessions window (days)", min_value=7, max_value=60, value=30, step=1)
    df_status = load_status_data(window_days=status_window)
    df_sessions = load_session_data(window_days=session_window)
    meta = load_charger_metadata()

    if df_status.empty:
        st.warning("No status data in the selected window.")
        return

    st.subheader(f"Overview (status last {status_window} days | sessions last {session_window} days)")
    col1, col2, col3 = st.columns(3)
    col1.metric("Status samples", f"{len(df_status):,}")
    col2.metric("Chargers", f"{df_status['charger_id'].nunique():,}")
    col3.metric("Sites", f"{df_status['site_id'].nunique():,}")

    per_charger = compute_reliability(df_status, level="charger")
    per_site = compute_reliability(df_status, level="site")

    # Business visuals
    st.subheader("Fault rate vs utilization (last 7 days)")
    fault_hourly = pd.read_sql(
        text(
            """
            SELECT date_trunc('hour', time) AS hour,
                   SUM(CASE WHEN status IN ('FAULTED','OFFLINE') THEN 1 ELSE 0 END)::float / COUNT(*) AS fault_rate
            FROM charger_status
            WHERE time >= now() - interval '7 days'
            GROUP BY 1
            ORDER BY 1
            """
        ),
        get_engine(),
    )
    util_hourly = pd.read_sql(
        text(
            """
            SELECT date_trunc('hour', start_time) AS hour,
                   SUM(duration_minutes) / 60.0 AS hours_used
            FROM charging_sessions
            WHERE start_time >= now() - interval '7 days'
            GROUP BY 1
            ORDER BY 1
            """
        ),
        get_engine(),
    )
    merged = fault_hourly.merge(util_hourly, on="hour", how="outer").fillna(0)
    merged["fault_rate_pct"] = merged["fault_rate"] * 100
    corr = merged[["fault_rate", "hours_used"]].corr().iloc[0, 1]
    fig_corr = px.line(
        merged,
        x="hour",
        y=["fault_rate_pct", "hours_used"],
        labels={"value": "Value", "variable": "Metric"},
        title=f"Fault rate vs utilization (last 7 days) | corr={corr:.3f}",
    )
    fig_corr.update_layout(yaxis_title="Fault rate (%) / Hours used", legend_title="")
    st.plotly_chart(fig_corr, use_container_width=True)

    st.subheader("Top chargers by fault rate (last 14 days)")
    top_fault = pd.read_sql(
        text(
            """
            SELECT
                c.external_id,
                s.name AS site_name,
                c.model,
                c.connector_type,
                COUNT(*) AS samples,
                SUM(CASE WHEN cs.status IN ('FAULTED','OFFLINE') THEN 1 ELSE 0 END)::float / COUNT(*) AS fault_rate
            FROM charger_status cs
            JOIN chargers c ON cs.charger_id = c.charger_id
            JOIN sites s ON c.site_id = s.site_id
            WHERE cs.time >= now() - interval '14 days'
            GROUP BY c.external_id, s.name, c.model, c.connector_type
            HAVING COUNT(*) > 0
            ORDER BY fault_rate DESC
            LIMIT 10
            """
        ),
        get_engine(),
    )
    fig_fault = px.bar(
        top_fault,
        x="external_id",
        y="fault_rate",
        color="site_name",
        hover_data=["model", "connector_type", "samples"],
        title="Top 10 chargers by fault rate (last 14 days)",
    )
    fig_fault.update_layout(yaxis_title="Fault rate", xaxis_title="Charger")
    st.plotly_chart(fig_fault, use_container_width=True)

    st.subheader("Top chargers by lost session minutes (last 30 days)")
    lost = pd.read_sql(
        text(
            """
            SELECT
                c.external_id,
                s.name AS site_name,
                c.model,
                c.connector_type,
                COUNT(*) AS sessions,
                SUM(duration_minutes) AS total_minutes,
                SUM(CASE WHEN success = false THEN duration_minutes ELSE 0 END) AS lost_minutes
            FROM charging_sessions cs
            JOIN chargers c ON cs.charger_id = c.charger_id
            JOIN sites s ON c.site_id = s.site_id
            WHERE cs.start_time >= now() - interval '30 days'
            GROUP BY c.external_id, s.name, c.model, c.connector_type
            HAVING SUM(CASE WHEN success = false THEN duration_minutes ELSE 0 END) > 0
            ORDER BY lost_minutes DESC
            LIMIT 10
            """
        ),
        get_engine(),
    )
    fig_lost = px.bar(
        lost,
        x="external_id",
        y="lost_minutes",
        color="site_name",
        hover_data=["model", "connector_type", "sessions", "total_minutes"],
        title="Top 10 chargers by lost session minutes (last 30 days)",
    )
    fig_lost.update_layout(yaxis_title="Lost minutes", xaxis_title="Charger")
    st.plotly_chart(fig_lost, use_container_width=True)

    st.subheader("Fault rate by model / connector (last 14 days)")
    model_faults = pd.read_sql(
        text(
            """
            SELECT
                c.model,
                c.connector_type,
                COUNT(*) AS samples,
                SUM(CASE WHEN cs.status IN ('FAULTED','OFFLINE') THEN 1 ELSE 0 END)::float / COUNT(*) AS fault_rate
            FROM charger_status cs
            JOIN chargers c ON cs.charger_id = c.charger_id
            WHERE cs.time >= now() - interval '14 days'
            GROUP BY c.model, c.connector_type
            HAVING COUNT(*) > 0
            ORDER BY fault_rate DESC
            """
        ),
        get_engine(),
    )
    fig_model = px.bar(
        model_faults,
        x="model",
        y="fault_rate",
        color="connector_type",
        barmode="group",
        title="Fault rate by model / connector (last 14 days)",
    )
    fig_model.update_layout(yaxis_title="Fault rate", xaxis_title="Model")
    st.plotly_chart(fig_model, use_container_width=True)

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


if __name__ == "__main__":
    main()
