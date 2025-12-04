"""Streamlit dashboard for charger reliability (uptime, fault hotspots)."""

import os
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
    return create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_use_lifo=True,
    )


@st.cache_data(ttl=60)
def load_status_overview(window_days: int) -> pd.DataFrame:
    """Lightweight overview counts computed in the database."""
    query = text(
        """
        SELECT
            COUNT(*) AS samples,
            COUNT(DISTINCT cs.charger_id) AS chargers,
            COUNT(DISTINCT c.site_id) AS sites
        FROM charger_status cs
        JOIN chargers c ON cs.charger_id = c.charger_id
        WHERE cs.time >= now() - interval :win
        """
    )
    return pd.read_sql(query, get_engine(), params={"win": f"{window_days} days"})


@st.cache_data(ttl=60)
def load_exec_kpis(status_window_days: int = 14, session_window_days: int = 30) -> dict:
    """Portfolio-level KPIs that fit on an exec summary strip."""
    status_sql = text(
        """
        SELECT
            SUM(CASE WHEN cs.status IN ('FAULTED','OFFLINE') THEN 1 ELSE 0 END) AS fault_samples,
            COUNT(*) AS samples,
            COUNT(DISTINCT cs.charger_id) AS chargers,
            COUNT(DISTINCT c.site_id) AS sites
        FROM charger_status cs
        JOIN chargers c ON cs.charger_id = c.charger_id
        WHERE cs.time >= now() - interval :win
        """
    )
    session_sql = text(
        """
        SELECT
            COUNT(*) AS sessions,
            COUNT(*) FILTER (WHERE success = false) AS failed_sessions,
            SUM(duration_minutes) AS total_minutes,
            SUM(duration_minutes) FILTER (WHERE success = false) AS lost_minutes
        FROM charging_sessions
        WHERE start_time >= now() - interval :win
        """
    )
    corr_sql = text(
        """
        WITH faults AS (
            SELECT date_trunc('day', time) AS day,
                   SUM(CASE WHEN status IN ('FAULTED','OFFLINE') THEN 1 ELSE 0 END)::float / COUNT(*) AS fault_rate
            FROM charger_status
            WHERE time >= now() - interval :win
            GROUP BY 1
        ),
        util AS (
            SELECT date_trunc('day', start_time) AS day,
                   COUNT(*) AS sessions
            FROM charging_sessions
            WHERE start_time >= now() - interval :win
            GROUP BY 1
        )
        SELECT corr(fault_rate, sessions) AS corr
        FROM faults
        JOIN util USING (day)
        """
    )
    status = pd.read_sql(status_sql, get_engine(), params={"win": f"{status_window_days} days"}).iloc[0].to_dict()
    sessions = pd.read_sql(session_sql, get_engine(), params={"win": f"{session_window_days} days"}).iloc[0].to_dict()
    corr_row = pd.read_sql(corr_sql, get_engine(), params={"win": f"{min(status_window_days, session_window_days)} days"})
    corr_val = float(corr_row.iloc[0]["corr"]) if not corr_row.empty and corr_row.iloc[0]["corr"] is not None else None
    return {
        "status": status,
        "sessions": sessions,
        "corr_load_fault": corr_val,
    }


@st.cache_data(ttl=60)
def load_reliability(level: Literal["charger", "site"], window_days: int) -> pd.DataFrame:
    """Compute reliability metrics in SQL and cache the result."""
    if level == "charger":
        select_cols = """
            c.charger_id,
            c.external_id,
            s.site_id,
            s.name AS site_name,
            c.model,
            c.connector_type
        """
        group_cols = "c.charger_id, c.external_id, s.site_id, s.name, c.model, c.connector_type"
    else:
        select_cols = "s.site_id, s.name AS site_name"
        group_cols = "s.site_id, s.name"

    query = text(
        f"""
        SELECT
            {select_cols},
            COUNT(*) AS samples,
            SUM(CASE WHEN cs.status IN ('FAULTED','OFFLINE') THEN 1 ELSE 0 END) AS fault_samples,
            SUM(CASE WHEN cs.status IN ('FAULTED','OFFLINE') THEN 1 ELSE 0 END)::float / NULLIF(COUNT(*), 0) AS fault_rate,
            (1 - SUM(CASE WHEN cs.status IN ('FAULTED','OFFLINE') THEN 1 ELSE 0 END)::float / NULLIF(COUNT(*), 0)) * 100 AS uptime_pct
        FROM charger_status cs
        JOIN chargers c ON cs.charger_id = c.charger_id
        JOIN sites s ON c.site_id = s.site_id
        WHERE cs.time >= now() - interval :win
        GROUP BY {group_cols}
        HAVING COUNT(*) > 0
        ORDER BY fault_rate DESC NULLS LAST
        """
    )
    return pd.read_sql(query, get_engine(), params={"win": f"{window_days} days"})


@st.cache_data(ttl=60)
def load_fault_vs_utilization() -> pd.DataFrame:
    """Join fault rate and utilization time series in SQL."""
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
    return merged


@st.cache_data(ttl=60)
def load_top_fault_chargers(window_days: int = 14) -> pd.DataFrame:
    query = text(
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
        WHERE cs.time >= now() - interval :win
        GROUP BY c.external_id, s.name, c.model, c.connector_type
        HAVING COUNT(*) > 0
        ORDER BY fault_rate DESC
        LIMIT 10
        """
    )
    return pd.read_sql(query, get_engine(), params={"win": f"{window_days} days"})


@st.cache_data(ttl=60)
def load_top_lost_minutes(window_days: int = 30) -> pd.DataFrame:
    query = text(
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
        WHERE cs.start_time >= now() - interval :win
        GROUP BY c.external_id, s.name, c.model, c.connector_type
        HAVING SUM(CASE WHEN success = false THEN duration_minutes ELSE 0 END) > 0
        ORDER BY lost_minutes DESC
        LIMIT 10
        """
    )
    return pd.read_sql(query, get_engine(), params={"win": f"{window_days} days"})


@st.cache_data(ttl=60)
def load_model_faults() -> pd.DataFrame:
    query = text(
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
    )
    return pd.read_sql(query, get_engine())


def main():
    st.set_page_config(page_title="Charger Reliability", layout="wide")
    st.title("Charger Reliability Dashboard")
    st.caption("Uptime and fault hotspots from charger_status (TimescaleDB)")

    status_window = st.sidebar.slider("Status window (days)", min_value=1, max_value=30, value=7, step=1)
    session_window = st.sidebar.slider("Sessions window (days)", min_value=7, max_value=60, value=30, step=1)

    exec_kpis = load_exec_kpis(status_window_days=max(status_window, 14), session_window_days=max(session_window, 30))
    total_samples = exec_kpis["status"].get("samples") or 0
    fault_samples = exec_kpis["status"].get("fault_samples") or 0
    uptime_pct = (1 - fault_samples / total_samples) * 100 if total_samples else 0
    lost_minutes = exec_kpis["sessions"].get("lost_minutes") or 0
    failed_sessions = exec_kpis["sessions"].get("failed_sessions") or 0
    total_sessions = exec_kpis["sessions"].get("sessions") or 0
    fault_corr = exec_kpis.get("corr_load_fault")

    st.subheader("Executive KPIs")
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Uptime (portfolio)", f"{uptime_pct:0.2f}%", help="1 - fault/offline rate over recent status window")
    col_b.metric("Lost minutes (30d)", f"{int(lost_minutes):,}", help="Failed session minutes (proxy for user impact)")
    fail_rate = (failed_sessions / total_sessions * 100) if total_sessions else 0
    col_c.metric("Session failure rate", f"{fail_rate:0.2f}%", help="Failed sessions / total sessions over recent window")
    corr_label = "N/A" if fault_corr is None else f"{fault_corr:0.3f}"
    col_d.metric("Load vs faults corr", corr_label, help="Correlation of daily sessions vs fault rate")
    st.markdown(
        "- Focus sites: use hotspots below to target maintenance; use load/fault corr to justify capacity.\n"
        "- User impact: lost minutes + failure rate quantify pain; MTTR/MTBF in notebooks for ops cadence."
    )

    overview = load_status_overview(status_window)
    if overview.empty or overview.loc[0, "samples"] == 0:
        st.warning("No status data in the selected window.")
        return

    st.subheader(f"Overview (status last {status_window} days | sessions last {session_window} days)")
    col1, col2, col3 = st.columns(3)
    col1.metric("Status samples", f"{int(overview.loc[0, 'samples']):,}")
    col2.metric("Chargers", f"{int(overview.loc[0, 'chargers']):,}")
    col3.metric("Sites", f"{int(overview.loc[0, 'sites']):,}")

    per_charger = load_reliability("charger", status_window)
    per_site = load_reliability("site", status_window)

    # Business visuals
    st.subheader("Fault rate vs utilization (last 7 days)")
    merged = load_fault_vs_utilization()
    corr = merged[["fault_rate", "hours_used"]].corr().iloc[0, 1] if not merged.empty else 0.0
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
    top_fault = load_top_fault_chargers(window_days=min(status_window, 14))
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
    lost = load_top_lost_minutes(window_days=session_window)
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
    model_faults = load_model_faults()
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
