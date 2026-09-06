#!/usr/bin/env python3
"""auth.py — Supabase JWT verification (server-side only).

The service_role key and JWT_SECRET never leave the server.
The anon key is exposed via /api/config and is safe to publish.
"""
from __future__ import annotations
import os
import jwt as pyjwt

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")


def dev_mode() -> bool:
    """True when Supabase is not configured — auth is bypassed entirely."""
    return not SUPABASE_URL


def verify_access_token(token: str) -> dict | None:
    """
    Verify a Supabase access_token (JWT signed with HS256).
    Returns the full payload on success, None on failure.

    Get SUPABASE_JWT_SECRET from:
      Supabase Dashboard → Settings → API → JWT Settings → JWT Secret
    """
    if not SUPABASE_JWT_SECRET:
        return None
    try:
        return pyjwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except pyjwt.PyJWTError:
        return None
