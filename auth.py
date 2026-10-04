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
_JWKS_CLIENT = None


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
    try:
        header = pyjwt.get_unverified_header(token)
        algorithm = header.get("alg")
        issuer = f"{SUPABASE_URL.rstrip('/')}/auth/v1" if SUPABASE_URL else None
        options = {"require": ["exp", "sub"]}
        if algorithm == "HS256" and SUPABASE_JWT_SECRET:
            return pyjwt.decode(
                token,
                SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated",
                issuer=issuer,
                options=options,
            )
        if algorithm in {"ES256", "RS256"} and SUPABASE_URL:
            # Supabase's current signing-key system publishes rotating public
            # keys through JWKS. PyJWKClient caches them between requests.
            global _JWKS_CLIENT
            if _JWKS_CLIENT is None:
                _JWKS_CLIENT = pyjwt.PyJWKClient(
                    f"{SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json",
                    cache_keys=True,
                    lifespan=600,
                )
            signing_key = _JWKS_CLIENT.get_signing_key_from_jwt(token)
            return pyjwt.decode(
                token,
                signing_key.key,
                algorithms=[algorithm],
                audience="authenticated",
                issuer=issuer,
                options=options,
            )
    except (pyjwt.PyJWTError, ValueError, OSError):
        return None
    return None
