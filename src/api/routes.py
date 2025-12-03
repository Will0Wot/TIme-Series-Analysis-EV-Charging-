"""API routes for EV charging analysis."""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from src.api.compat import patch_pydantic_forward_refs

patch_pydantic_forward_refs()

from fastapi import APIRouter, HTTPException  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.database.timescale import db  # noqa: E402

router = APIRouter()


def _to_float(value: Optional[Decimal]) -> Optional[float]:
    if value is None:
        return None
    return float(value)


class ChargerInfo(BaseModel):
    charger_id: int
    external_id: str
    site_id: int
    site_name: str
    city: str
    country: str
    model: Optional[str] = None
    connector_type: str
    max_power_kw: float
    last_status: Optional[str] = None
    last_seen: Optional[datetime] = None


class SessionSummary(BaseModel):
    session_id: str
    start_time: datetime
    end_time: datetime
    duration_minutes: int
    energy_kwh: float
    success: bool
    stop_reason: Optional[str] = None


class ChargerStats(BaseModel):
    charger: ChargerInfo
    sessions: int
    total_energy_kwh: float
    avg_duration_minutes: Optional[float]
    first_session: Optional[datetime]
    last_session: Optional[datetime]
    last_status: Optional[str]
    last_status_time: Optional[datetime]
    recent_sessions: List[SessionSummary]


class ActiveAlert(BaseModel):
    site_id: int
    site_name: str
    charger_id: int
    external_id: str
    status: str
    start_time: datetime
    last_seen: datetime
    duration_minutes: float
    model: Optional[str] = None
    connector_type: Optional[str] = None


class ReliabilityMetric(BaseModel):
    scope: str  # "site" or "model"
    key: str
    uptime_pct: float
    mtbf_minutes: Optional[float]
    mttr_minutes: Optional[float]
    fault_rate: float
    samples: int


@router.get("/api/chargers", response_model=List[ChargerInfo])
async def list_chargers():
    """Get list of all chargers with latest status."""

    def _query() -> List[dict]:
        session = db.get_session()
        try:
            rows = session.execute(
                text(
                    """
                    SELECT
                        c.charger_id,
                        c.external_id,
                        c.model,
                        c.max_power_kw,
                        c.connector_type,
                        c.site_id,
                        s.name AS site_name,
                        s.city,
                        s.country,
                        ls.status AS last_status,
                        ls.time AS last_seen
                    FROM chargers c
                    JOIN sites s ON c.site_id = s.site_id
                    LEFT JOIN LATERAL (
                        SELECT status, time
                        FROM charger_status cs
                        WHERE cs.charger_id = c.charger_id
                        ORDER BY time DESC
                        LIMIT 1
                    ) ls ON true
                    ORDER BY c.charger_id;
                    """
                )
            ).mappings().all()
            chargers = []
            for row in rows:
                item = dict(row)
                item["max_power_kw"] = _to_float(item.get("max_power_kw")) or 0.0
                chargers.append(item)
            return chargers
        finally:
            session.close()

    return await run_in_threadpool(_query)


@router.get("/api/chargers/{charger_id}/stats", response_model=ChargerStats)
async def get_charger_stats(charger_id: int):
    """Get statistics and recent activity for a specific charger."""

    def _query() -> dict:
        session = db.get_session()
        try:
            charger_row = session.execute(
                text(
                    """
                    SELECT
                        c.charger_id,
                        c.external_id,
                        c.model,
                        c.max_power_kw,
                        c.connector_type,
                        c.site_id,
                        s.name AS site_name,
                        s.city,
                        s.country
                    FROM chargers c
                    JOIN sites s ON c.site_id = s.site_id
                    WHERE c.charger_id = :cid
                    """
                ),
                {"cid": charger_id},
            ).mappings().first()

            if not charger_row:
                raise HTTPException(status_code=404, detail="Charger not found")

            last_status = session.execute(
                text(
                    """
                    SELECT status, time
                    FROM charger_status
                    WHERE charger_id = :cid
                    ORDER BY time DESC
                    LIMIT 1
                    """
                ),
                {"cid": charger_id},
            ).mappings().first()

            session_summary = session.execute(
                text(
                    """
                    SELECT
                        count(*) AS sessions,
                        sum(energy_kwh) AS total_energy_kwh,
                        avg(duration_minutes) AS avg_duration_minutes,
                        min(start_time) AS first_session,
                        max(end_time) AS last_session
                    FROM charging_sessions
                    WHERE charger_id = :cid
                    """
                ),
                {"cid": charger_id},
            ).mappings().first()

            recent_sessions = session.execute(
                text(
                    """
                    SELECT
                        session_id,
                        start_time,
                        end_time,
                        duration_minutes,
                        energy_kwh,
                        success,
                        stop_reason
                    FROM charging_sessions
                    WHERE charger_id = :cid
                    ORDER BY start_time DESC
                    LIMIT 5
                    """
                ),
                {"cid": charger_id},
            ).mappings().all()

            charger_info = dict(charger_row)
            charger_info["max_power_kw"] = _to_float(charger_info.get("max_power_kw")) or 0.0
            if last_status:
                charger_info["last_status"] = last_status["status"]
                charger_info["last_seen"] = last_status["time"]
            else:
                charger_info["last_status"] = None
                charger_info["last_seen"] = None

            return {
                "charger": charger_info,
                "sessions": int(session_summary["sessions"] or 0),
                "total_energy_kwh": _to_float(session_summary["total_energy_kwh"]) or 0.0,
                "avg_duration_minutes": _to_float(session_summary["avg_duration_minutes"]),
                "first_session": session_summary["first_session"],
                "last_session": session_summary["last_session"],
                "last_status": last_status["status"] if last_status else None,
                "last_status_time": last_status["time"] if last_status else None,
                "recent_sessions": [
                    {
                        **dict(row),
                        "energy_kwh": _to_float(row["energy_kwh"]) or 0.0,
                    }
                    for row in recent_sessions
                ],
            }
        finally:
            session.close()

    return await run_in_threadpool(_query)


@router.get("/api/alerts", response_model=List[ActiveAlert])
async def get_active_alerts():
    """Return chargers currently FAULTED or OFFLINE with their window duration."""

    def _query() -> List[dict]:
        session = db.get_session()
        try:
            rows = session.execute(
                text(
                    """
                    WITH ordered AS (
                        SELECT
                            cs.charger_id,
                            cs.status,
                            cs.time,
                            c.external_id,
                            c.model,
                            c.connector_type,
                            s.site_id,
                            s.name AS site_name,
                            lag(cs.status) over (partition by cs.charger_id order by cs.time) as prev_status,
                            lag(cs.time) over (partition by cs.charger_id order by cs.time) as prev_time
                        FROM charger_status cs
                        JOIN chargers c ON cs.charger_id = c.charger_id
                        JOIN sites s ON c.site_id = s.site_id
                        WHERE cs.time >= now() - interval '3 days'
                    ),
                    windows AS (
                        SELECT *,
                            CASE
                                WHEN status IN ('FAULTED','OFFLINE')
                                     AND (prev_status IS NULL OR prev_status NOT IN ('FAULTED','OFFLINE'))
                                THEN 1 ELSE 0 END AS is_start
                        FROM ordered
                    ),
                    grouped AS (
                        SELECT *,
                            sum(is_start) over (partition by charger_id order by time) as grp
                        FROM windows
                    ),
                    active AS (
                        SELECT
                            charger_id,
                            external_id,
                            model,
                            connector_type,
                            site_id,
                            site_name,
                            status,
                            min(time) as start_time,
                            max(time) as last_seen
                        FROM grouped
                        WHERE status IN ('FAULTED','OFFLINE')
                        GROUP BY charger_id, external_id, model, connector_type, site_id, site_name, status, grp
                    )
                    SELECT
                        charger_id,
                        external_id,
                        model,
                        connector_type,
                        site_id,
                        site_name,
                        status,
                        start_time,
                        last_seen,
                        EXTRACT(EPOCH FROM (last_seen - start_time))/60.0 AS duration_minutes
                    FROM active
                    WHERE last_seen >= now() - interval '30 minutes'
                    ORDER BY duration_minutes DESC;
                    """
                )
            ).mappings().all()

            return [dict(r) for r in rows]
        finally:
            session.close()

    return await run_in_threadpool(_query)


@router.get("/api/reliability", response_model=List[ReliabilityMetric])
async def get_reliability_metrics(scope: str = "site", days: int = 7):
    """Uptime/fault metrics and MTBF/MTTR grouped by site or model."""
    if scope not in {"site", "model"}:
        raise HTTPException(status_code=400, detail="scope must be 'site' or 'model'")

    def _query() -> List[dict]:
        session = db.get_session()
        try:
            if scope == "site":
                group_cols = "site_id, site_name"
                key_expr = "fr.site_name"
                join_condition = "fr.site_id = m.site_id AND fr.site_name = m.site_name"
            else:
                group_cols = "model, connector_type"
                key_expr = "fr.model"
                join_condition = "fr.model = m.model AND fr.connector_type = m.connector_type"
            data = session.execute(
                text(
                    f"""
                    WITH recent AS (
                        SELECT
                            cs.charger_id,
                            cs.status,
                            cs.time,
                            c.model,
                            c.connector_type,
                            s.site_id,
                            s.name as site_name,
                            lead(cs.time) over (partition by cs.charger_id order by cs.time) as next_time,
                            lag(cs.status) over (partition by cs.charger_id order by cs.time) as prev_status
                        FROM charger_status cs
                        JOIN chargers c ON cs.charger_id = c.charger_id
                        JOIN sites s ON c.site_id = s.site_id
                        WHERE cs.time >= now() - interval :win
                    ),
                    durations AS (
                        SELECT
                            {group_cols},
                            status,
                            EXTRACT(EPOCH FROM (COALESCE(next_time, now()) - time))/60.0 as minutes
                        FROM recent
                    ),
                    mttr_mtbf AS (
                        SELECT
                            {group_cols},
                            AVG(CASE WHEN status IN ('FAULTED','OFFLINE') THEN minutes END) as mttr_minutes,
                            AVG(CASE WHEN status NOT IN ('FAULTED','OFFLINE') THEN minutes END) as mtbf_minutes
                        FROM durations
                        GROUP BY {group_cols}
                    ),
                    fault_rates AS (
                        SELECT
                            {group_cols},
                            COUNT(*) AS samples,
                            SUM(CASE WHEN status IN ('FAULTED','OFFLINE') THEN 1 ELSE 0 END) AS faults
                        FROM recent
                        GROUP BY {group_cols}
                    )
                    SELECT
                        {key_expr} AS key,
                        {f"'{scope}'"} AS scope,
                        fr.samples,
                        fr.faults::float / NULLIF(fr.samples,0) AS fault_rate,
                        (1 - fr.faults::float / NULLIF(fr.samples,0)) * 100 AS uptime_pct,
                        m.mtbf_minutes,
                        m.mttr_minutes
                    FROM fault_rates fr
                    JOIN mttr_mtbf m ON ({join_condition})
                    ORDER BY fault_rate DESC NULLS LAST;
                    """
                ),
                {"win": f"{days} days"},
            ).mappings().all()

            results = []
            for row in data:
                results.append(
                    {
                        "scope": scope,
                        "key": row["key"] or "Unknown",
                        "samples": int(row["samples"] or 0),
                        "fault_rate": float(row["fault_rate"] or 0.0),
                        "uptime_pct": float(row["uptime_pct"] or 0.0),
                        "mtbf_minutes": _to_float(row["mtbf_minutes"]),
                        "mttr_minutes": _to_float(row["mttr_minutes"]),
                    }
                )
            return results
        finally:
            session.close()

    return await run_in_threadpool(_query)


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}
