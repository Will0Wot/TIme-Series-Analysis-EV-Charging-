"""API routes for EV charging analysis."""
from datetime import datetime
from decimal import Decimal
import time
from typing import Dict, List, Optional, Tuple

from src.api.compat import patch_pydantic_forward_refs

patch_pydantic_forward_refs()

from fastapi import APIRouter, HTTPException, Response  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.database.timescale import db  # noqa: E402

router = APIRouter()

# Simple TTL cache for reliability metrics to avoid recomputing heavy window functions
_reliability_cache: Dict[Tuple[str, int], Tuple[float, List[dict]]] = {}
_RELIABILITY_TTL_SECONDS = 60


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
async def list_chargers(response: Response):
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

    data = await run_in_threadpool(_query)
    response.headers["Cache-Control"] = "public, max-age=30"
    return data


@router.get("/api/chargers/{charger_id}/stats", response_model=ChargerStats)
async def get_charger_stats(charger_id: int):
    """Get statistics and recent activity for a specific charger with one DB round-trip."""

    def _query() -> dict:
        session = db.get_session()
        try:
            row = session.execute(
                text(
                    """
                    WITH base AS (
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
                    ),
                    agg AS (
                        SELECT
                            COUNT(*) AS sessions,
                            SUM(energy_kwh) AS total_energy_kwh,
                            AVG(duration_minutes) AS avg_duration_minutes,
                            MIN(start_time) AS first_session,
                            MAX(end_time) AS last_session
                        FROM charging_sessions
                        WHERE charger_id = :cid
                    ),
                    last_status AS (
                        SELECT status, time
                        FROM charger_status
                        WHERE charger_id = :cid
                        ORDER BY time DESC
                        LIMIT 1
                    ),
                    recent_sessions AS (
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
                    )
                    SELECT
                        b.charger_id,
                        b.external_id,
                        b.model,
                        b.max_power_kw,
                        b.connector_type,
                        b.site_id,
                        b.site_name,
                        b.city,
                        b.country,
                        ls.status AS last_status,
                        ls.time AS last_seen,
                        agg.sessions,
                        agg.total_energy_kwh,
                        agg.avg_duration_minutes,
                        agg.first_session,
                        agg.last_session,
                        ls.status AS last_status_value,
                        ls.time AS last_status_time,
                        COALESCE(
                            (
                                SELECT json_agg(r ORDER BY r.start_time DESC)
                                FROM (
                                    SELECT
                                        session_id,
                                        start_time,
                                        end_time,
                                        duration_minutes,
                                        energy_kwh,
                                        success,
                                        stop_reason
                                    FROM recent_sessions
                                ) r
                            ), '[]'::json
                        ) AS recent_sessions
                    FROM base b
                    LEFT JOIN agg ON TRUE
                    LEFT JOIN last_status ls ON TRUE;
                    """
                ),
                {"cid": charger_id},
            ).mappings().first()

            if not row:
                raise HTTPException(status_code=404, detail="Charger not found")

            charger_info = {
                key: row[key]
                for key in [
                    "charger_id",
                    "external_id",
                    "model",
                    "max_power_kw",
                    "connector_type",
                    "site_id",
                    "site_name",
                    "city",
                    "country",
                    "last_status",
                    "last_seen",
                ]
            }
            charger_info["max_power_kw"] = _to_float(charger_info.get("max_power_kw")) or 0.0

            recent_sessions = [
                {
                    **sess,
                    "energy_kwh": _to_float(sess.get("energy_kwh")) or 0.0,
                }
                for sess in (row["recent_sessions"] or [])
            ]

            return {
                "charger": charger_info,
                "sessions": int(row.get("sessions") or 0),
                "total_energy_kwh": _to_float(row.get("total_energy_kwh")) or 0.0,
                "avg_duration_minutes": _to_float(row.get("avg_duration_minutes")),
                "first_session": row.get("first_session"),
                "last_session": row.get("last_session"),
                "last_status": row.get("last_status_value"),
                "last_status_time": row.get("last_status_time"),
                "recent_sessions": recent_sessions,
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
async def get_reliability_metrics(scope: str = "site", days: int = 7, response: Response = None):
    """Uptime/fault metrics and MTBF/MTTR grouped by site or model with a short TTL cache."""
    if scope not in {"site", "model"}:
        raise HTTPException(status_code=400, detail="scope must be 'site' or 'model'")

    cache_key = (scope, days)
    now_ts = time.time()
    cached = _reliability_cache.get(cache_key)
    if cached and (now_ts - cached[0]) < _RELIABILITY_TTL_SECONDS:
        if response:
            response.headers["Cache-Control"] = f"public, max-age={_RELIABILITY_TTL_SECONDS}"
        return cached[1]

    def _query() -> List[dict]:
        session = db.get_session()
        try:
            view_exists = bool(session.execute(text("SELECT to_regclass('charger_status_hourly')")).scalar())
            if scope == "site":
                group_cols = "site_id, site_name"
                key_expr = "metric.site_name"
                join_condition = "metric.site_id = meta.site_id AND metric.site_name = meta.site_name"
            else:
                group_cols = "model, connector_type"
                key_expr = "metric.model"
                join_condition = "metric.model = meta.model AND metric.connector_type = meta.connector_type"

            view_cte = ""
            if view_exists:
                view_cte = f"""
                        SELECT
                            {group_cols},
                            SUM(fault_minutes) AS fault_minutes,
                            SUM(total_minutes) AS total_minutes,
                            SUM(samples) AS samples,
                            AVG(mtbf_minutes) AS mtbf_minutes,
                            AVG(mttr_minutes) AS mttr_minutes
                        FROM charger_status_hourly
                        WHERE bucket >= now() - interval :win
                        GROUP BY {group_cols}
                        UNION ALL
                """

            data = session.execute(
                text(
                    f"""
                    -- Prefer continuous aggregate if present, otherwise fall back to raw window
                    WITH metric AS (
                        {view_cte}
                        SELECT
                            {group_cols},
                            SUM(CASE WHEN status IN ('FAULTED','OFFLINE') THEN EXTRACT(EPOCH FROM (COALESCE(lead(time) over (partition by charger_id order by time), now()) - time))/60.0 ELSE 0 END) AS fault_minutes,
                            SUM(EXTRACT(EPOCH FROM (COALESCE(lead(time) over (partition by charger_id order by time), now()) - time))/60.0) AS total_minutes,
                            COUNT(*) AS samples,
                            AVG(CASE WHEN status NOT IN ('FAULTED','OFFLINE') THEN EXTRACT(EPOCH FROM (COALESCE(lead(time) over (partition by charger_id order by time), now()) - time))/60.0 END) AS mtbf_minutes,
                            AVG(CASE WHEN status IN ('FAULTED','OFFLINE') THEN EXTRACT(EPOCH FROM (COALESCE(lead(time) over (partition by charger_id order by time), now()) - time))/60.0 END) AS mttr_minutes
                        FROM charger_status cs
                        JOIN chargers c ON cs.charger_id = c.charger_id
                        JOIN sites s ON c.site_id = s.site_id
                        WHERE cs.time >= now() - interval :win
                        GROUP BY {group_cols}
                    ),
                    metric_ranked AS (
                        SELECT *, row_number() over (partition by {group_cols} order by total_minutes DESC) AS rk
                        FROM metric
                    ),
                    meta AS (
                        SELECT DISTINCT {group_cols}
                        FROM chargers c
                        JOIN sites s ON c.site_id = s.site_id
                    )
                    SELECT
                        {key_expr} AS key,
                        {f"'{scope}'"} AS scope,
                        m.samples,
                        CASE WHEN m.total_minutes = 0 THEN 0 ELSE m.fault_minutes / m.total_minutes END AS fault_rate,
                        (1 - CASE WHEN m.total_minutes = 0 THEN 0 ELSE m.fault_minutes / m.total_minutes END) * 100 AS uptime_pct,
                        m.mtbf_minutes,
                        m.mttr_minutes
                    FROM metric_ranked m
                    JOIN meta ON ({join_condition})
                    WHERE m.rk = 1
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

    results = await run_in_threadpool(_query)
    _reliability_cache[cache_key] = (now_ts, results)
    if response:
        response.headers["Cache-Control"] = f"public, max-age={_RELIABILITY_TTL_SECONDS}"
    return results


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}
