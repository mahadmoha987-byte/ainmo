#!/usr/bin/env python3
"""
db.py — Supabase REST persistence layer.

All writes use the service_role key, which bypasses RLS.
RLS still applies for any direct client access (defence-in-depth).
"""
from __future__ import annotations

import os
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

import httpx
from regulatory import REGULATORY_VERSION

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
FREE_MONTHLY_LIMIT = 5
DECREE_VERSION = REGULATORY_VERSION

_suggest_client: httpx.AsyncClient | None = None
_SUGGEST_CACHE_TTL = 60.0
_SUGGEST_CACHE_MAX = 2_000
_suggest_cache: OrderedDict[tuple, tuple[float, list[dict]]] = OrderedDict()


def _ok() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def _h(*, prefer: str | None = None) -> dict:
    h = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def _url(table: str, qs: str = "") -> str:
    base = f"{SUPABASE_URL}/rest/v1/{table}"
    return f"{base}?{qs}" if qs else base


def _rpc(fn: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/rpc/{fn}"


def _month() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m")


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


async def init() -> None:
    global _suggest_client
    if _ok() and _suggest_client is None:
        # Reuse one keep-alive connection pool for latency-sensitive calls.
        # The rest of the persistence layer remains intentionally unchanged.
        _suggest_client = httpx.AsyncClient(
            timeout=httpx.Timeout(2.5, connect=1.5),
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
        )


async def close() -> None:
    global _suggest_client
    if _suggest_client is not None:
        await _suggest_client.aclose()
        _suggest_client = None


# ── Address autocomplete ─────────────────────────────────────────────────────

async def search_address_index(
    normalized_query: str,
    *,
    lat: float | None = None,
    lng: float | None = None,
    recent_lot_codes: list[str] | None = None,
    limit: int = 8,
) -> tuple[list[dict], bool]:
    """Return ranked local address suggestions and whether the index is ready.

    The PostgreSQL RPC owns ranking/index use.  ``False`` is returned for the
    availability flag when the migration has not been applied yet, allowing
    the existing full geocoder to remain usable during a rolling deployment.
    """
    if not _ok():
        return [], False

    clean_recent = tuple(
        code for code in (recent_lot_codes or [])
        if isinstance(code, str) and code.isdigit() and len(code) == 12
    )[:6]
    safe_limit = min(max(int(limit), 1), 8)
    cache_key = (
        normalized_query,
        round(lat, 3) if lat is not None else None,
        round(lng, 3) if lng is not None else None,
        clean_recent,
        safe_limit,
    )
    cached = _suggest_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _SUGGEST_CACHE_TTL:
        _suggest_cache.move_to_end(cache_key)
        return [dict(row) for row in cached[1]], True

    payload = {
        "p_query": normalized_query,
        "p_lat": lat,
        "p_lng": lng,
        "p_recent_lot_codes": list(clean_recent),
        "p_limit": safe_limit,
    }
    temporary_client = _suggest_client is None
    client = _suggest_client or httpx.AsyncClient(timeout=2.5)
    try:
        response = await client.post(
            _rpc("search_address_index"),
            headers=_h(),
            json=payload,
        )
    except httpx.HTTPError:
        return [], False
    finally:
        if temporary_client:
            await client.aclose()

    if response.status_code >= 400:
        # Missing table/function during migration is a feature-availability
        # state, not a user-facing address-search failure.
        return [], False
    try:
        rows = response.json()
    except ValueError:
        return [], False
    if not isinstance(rows, list):
        return [], False

    suggestions: list[dict] = []
    for row in rows[:safe_limit]:
        try:
            suggestion = {
                "address": str(row["address"]),
                "normalized_address": str(row["normalized_address"]),
                "lot_code": str(row["lot_code"]),
                "treatment": str(row["treatment"]) if row.get("treatment") else None,
                "lat": float(row["lat"]),
                "lng": float(row["lng"]),
                "locality": str(row["locality"]) if row.get("locality") else None,
                "neighborhood": str(row["neighborhood"]) if row.get("neighborhood") else None,
            }
        except (KeyError, TypeError, ValueError):
            continue
        suggestions.append(suggestion)

    _suggest_cache[cache_key] = (time.monotonic(), suggestions)
    _suggest_cache.move_to_end(cache_key)
    while len(_suggest_cache) > _SUGGEST_CACHE_MAX:
        _suggest_cache.popitem(last=False)
    return [dict(row) for row in suggestions], True


# ── Profiles ──────────────────────────────────────────────────────────────────

async def get_profile(user_id: str) -> dict | None:
    if not _ok():
        return None
    async with httpx.AsyncClient() as c:
        r = await c.get(
            _url("profiles", f"id=eq.{user_id}&select=id,email,name,avatar_url,plan"),
            headers=_h(),
        )
    rows = r.json()
    return rows[0] if isinstance(rows, list) and rows else None


async def ensure_profile(user_id: str, email: str) -> dict:
    """Belt-and-suspenders: upsert in case the trigger missed the first sign-in."""
    if not _ok():
        return {"id": user_id, "email": email, "plan": "free"}
    async with httpx.AsyncClient() as c:
        r = await c.post(
            _url("profiles"),
            headers=_h(prefer="return=representation,resolution=ignore-duplicates"),
            json={"id": user_id, "email": email},
        )
    rows = r.json()
    if isinstance(rows, list) and rows:
        return rows[0]
    return await get_profile(user_id) or {"id": user_id, "email": email, "plan": "free"}


async def set_plan(user_id: str, plan: str) -> None:
    """Billing hook: flip user plan. Call with 'pro' or 'free'."""
    async with httpx.AsyncClient() as c:
        await c.patch(
            _url("profiles", f"id=eq.{user_id}"),
            headers=_h(),
            json={"plan": plan},
        )


# ── Usage ─────────────────────────────────────────────────────────────────────

async def get_usage(user_id: str) -> tuple[int, int]:
    """Return (used_this_month, monthly_limit). limit=0 means unlimited (pro)."""
    profile = await get_profile(user_id)
    if not profile or profile.get("plan") == "pro":
        return (0, 0)
    async with httpx.AsyncClient() as c:
        r = await c.get(
            _url("usage", f"user_id=eq.{user_id}&month=eq.{_month()}&select=lookup_count"),
            headers=_h(),
        )
    rows = r.json()
    used = rows[0]["lookup_count"] if isinstance(rows, list) and rows else 0
    return (used, FREE_MONTHLY_LIMIT)


async def check_usage_allowed(user_id: str) -> bool:
    used, limit = await get_usage(user_id)
    return limit == 0 or used < limit


async def increment_usage(user_id: str) -> int:
    """Atomically increment counter via RPC. Returns new count, -1 for pro."""
    profile = await get_profile(user_id)
    if not profile or profile.get("plan") == "pro":
        return -1
    async with httpx.AsyncClient() as c:
        r = await c.post(
            _rpc("increment_usage"),
            headers=_h(),
            json={"p_user_id": user_id, "p_month": _month()},
        )
    return r.json() if isinstance(r.json(), int) else 1


# ── Analyses ──────────────────────────────────────────────────────────────────

async def save_analysis(
    user_id: str,
    direccion: str,
    lat: float,
    lng: float,
    lookup_snapshot: dict,
    calc_result: dict,
    vis_en_sitio: bool = False,
    anu_m2: float | None = None,
    notas: str = "",
    tags: list[str] | None = None,
) -> str:
    lote_codigo = lookup_snapshot.get("lote", {}).get("lotcodigo") or None
    payload = {
        "user_id": user_id,
        "direccion": direccion,
        "lote_codigo": lote_codigo,
        "lat": lat,
        "lng": lng,
        "result_json": {
            "lookup_snapshot": lookup_snapshot,
            "calc_result": calc_result,
            "vis_en_sitio": vis_en_sitio,
        },
        "decree_version": DECREE_VERSION,
        "fetched_at": _now(),
        "notas": notas,
        "tags": tags or [],
    }
    async with httpx.AsyncClient() as c:
        r = await c.post(
            _url("analyses"),
            headers=_h(prefer="return=representation"),
            json=payload,
        )
    rows = r.json()
    return rows[0]["id"] if isinstance(rows, list) and rows else ""


async def get_portfolio_data(user_id: str, limit: int = 500) -> list[dict]:
    """Full analyses including result_json — used by the portfolio dashboard."""
    if not _ok():
        return []
    async with httpx.AsyncClient() as c:
        r = await c.get(
            _url(
                "analyses",
                f"user_id=eq.{user_id}"
                f"&select=id,direccion,lote_codigo,lat,lng,result_json,decree_version,fetched_at,created_at,notas,tags"
                f"&order=created_at.desc&limit={limit}",
            ),
            headers=_h(),
        )
    return r.json() if isinstance(r.json(), list) else []


async def list_analyses(user_id: str, limit: int = 30) -> list[dict]:
    async with httpx.AsyncClient() as c:
        r = await c.get(
            _url(
                "analyses",
                f"user_id=eq.{user_id}"
                f"&select=id,direccion,lote_codigo,lat,lng,decree_version,fetched_at,created_at,notas,tags"
                f"&order=created_at.desc&limit={limit}",
            ),
            headers=_h(),
        )
    return r.json() if isinstance(r.json(), list) else []


async def get_analysis(analysis_id: str, user_id: str) -> dict | None:
    async with httpx.AsyncClient() as c:
        r = await c.get(
            _url("analyses", f"id=eq.{analysis_id}&user_id=eq.{user_id}&select=*"),
            headers=_h(),
        )
    rows = r.json()
    return rows[0] if isinstance(rows, list) and rows else None


async def update_analysis_notas(analysis_id: str, user_id: str, notas: str) -> None:
    async with httpx.AsyncClient() as c:
        await c.patch(
            _url("analyses", f"id=eq.{analysis_id}&user_id=eq.{user_id}"),
            headers=_h(),
            json={"notas": notas},
        )


async def update_analysis_tags(analysis_id: str, user_id: str, tags: list[str]) -> None:
    async with httpx.AsyncClient() as c:
        await c.patch(
            _url("analyses", f"id=eq.{analysis_id}&user_id=eq.{user_id}"),
            headers=_h(),
            json={"tags": tags},
        )


async def delete_analysis(analysis_id: str, user_id: str) -> None:
    async with httpx.AsyncClient() as c:
        await c.delete(
            _url("analyses", f"id=eq.{analysis_id}&user_id=eq.{user_id}"),
            headers=_h(),
        )


# ── Share tokens ──────────────────────────────────────────────────────────────
# Requires table in Supabase:
#   CREATE TABLE share_tokens (
#     token text PRIMARY KEY,
#     analysis_id uuid REFERENCES analyses(id) ON DELETE CASCADE,
#     include_proforma boolean DEFAULT false,
#     created_at timestamptz DEFAULT now()
#   );
#   ALTER TABLE share_tokens ENABLE ROW LEVEL SECURITY;
#   CREATE POLICY "public_read" ON share_tokens FOR SELECT USING (true);

async def create_share_token(analysis_id: str, include_proforma: bool = False) -> str:
    import secrets as _sec
    token = _sec.token_urlsafe(16)
    if not _ok():
        return token
    async with httpx.AsyncClient() as c:
        await c.post(
            _url("share_tokens"),
            headers=_h(prefer="return=minimal"),
            json={"token": token, "analysis_id": analysis_id, "include_proforma": include_proforma},
        )
    return token


async def get_share_token_analysis(token: str) -> dict | None:
    if not _ok():
        return None
    async with httpx.AsyncClient() as c:
        r = await c.get(
            _url("share_tokens", f"token=eq.{token}&select=analysis_id,include_proforma"),
            headers=_h(),
        )
    rows = r.json()
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    async with httpx.AsyncClient() as c:
        r = await c.get(
            _url("analyses", f"id=eq.{row['analysis_id']}&select=*"),
            headers=_h(),
        )
    rows = r.json()
    if not isinstance(rows, list) or not rows:
        return None
    return {"analysis": rows[0], "include_proforma": row["include_proforma"]}


# ── Billing interest ──────────────────────────────────────────────────────────

async def add_billing_interest(email: str, notes: str = "") -> None:
    async with httpx.AsyncClient() as c:
        await c.post(
            _url("billing_interest"),
            headers=_h(),
            json={"email": email, "notes": notes},
        )
