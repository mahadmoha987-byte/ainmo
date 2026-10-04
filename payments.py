"""Stripe Checkout integration for one-off Ainmo PDF reports.

The paid entitlement is the Stripe Checkout Session itself.  Report identity is
stored as session metadata and verified server-to-server before PDF generation,
so a buyer cannot pay for one lot and alter the download URL to request another.
"""

from __future__ import annotations

import os
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx


STRIPE_API = "https://api.stripe.com/v1"
PRICE_COP = int(os.getenv("PDF_REPORT_PRICE_COP", "99000"))
SUBSCRIPTION_PLANS = {
    "pro": {
        "price_id": os.getenv("STRIPE_PRO_PRICE_ID", "").strip(),
        "label": "Ainmo Pro",
        "display_price_cop": int(os.getenv("PRO_MONTHLY_PRICE_COP", "0")),
    },
    "team": {
        "price_id": os.getenv("STRIPE_TEAM_PRICE_ID", "").strip(),
        "label": "Ainmo Equipos",
        "display_price_cop": int(os.getenv("TEAM_MONTHLY_PRICE_COP", "0")),
    },
}


class PaymentError(RuntimeError):
    pass


def _flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def configured() -> bool:
    """Whether Stripe credentials exist, without implying billing is live."""
    return bool(os.getenv("STRIPE_SECRET_KEY", "").strip())


def enabled() -> bool:
    """Billing is opt-in so adding Stripe credentials cannot create a paywall."""
    return configured() and _flag("AINMO_BILLING_ENABLED", default=False)


def public_config() -> dict[str, Any]:
    return {
        "enabled": enabled(),
        "configured": configured(),
        "price_cop": PRICE_COP,
        "currency": "COP",
        "subscriptions_enabled": subscriptions_enabled(),
        "subscription_plans": {
            key: {
                "label": value["label"],
                "price_cop_month": value["display_price_cop"],
                "available": enabled() and bool(value["price_id"]),
            }
            for key, value in SUBSCRIPTION_PLANS.items()
        },
    }


def subscriptions_enabled() -> bool:
    return enabled() and any(plan["price_id"] for plan in SUBSCRIPTION_PLANS.values())


def _app_url() -> str:
    return os.getenv("APP_URL", "https://ainmo.uk").rstrip("/")


def _minor_units(cop: int) -> int:
    # Stripe treats COP as a two-decimal currency.  API amounts are submitted
    # in the minor unit, even though consumer prices are conventionally whole COP.
    return cop * 100


def _metadata(context: dict[str, Any]) -> dict[str, str]:
    allowed = (
        "lat", "lng", "vis_en_sitio", "anu_m2", "frente_m", "ancho_via_m",
        "address", "searched_address", "resolved_address", "searched_chip",
        "near_match", "search_mode", "expected_lotcodigo",
    )
    metadata = {"product": "ainmo_pdf_report", "schema": "1"}
    for key in allowed:
        value = context.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        metadata[key] = str(value)[:500]
    return metadata


async def _stripe_request(
    method: str, path: str, *, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    secret = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not secret:
        raise PaymentError("El cobro de informes todavía no está habilitado.")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(
            method,
            f"{STRIPE_API}{path}",
            data=data,
            auth=(secret, ""),
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise PaymentError("Stripe devolvió una respuesta inválida.") from exc
    if response.status_code >= 400:
        message = (payload.get("error") or {}).get("message") or "Stripe rechazó la solicitud."
        raise PaymentError(message)
    return payload


async def create_pdf_checkout(context: dict[str, Any]) -> dict[str, Any]:
    if not enabled():
        raise PaymentError("Los cobros no están habilitados durante la beta abierta.")
    metadata = _metadata(context)
    data: dict[str, Any] = {
        "mode": "payment",
        "locale": "es",
        "submit_type": "pay",
        "success_url": f"{_app_url()}/checkout/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{_app_url()}/app?checkout=cancelled",
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "cop",
        "line_items[0][price_data][unit_amount]": str(_minor_units(PRICE_COP)),
        "line_items[0][price_data][product_data][name]": "Informe de prefactibilidad urbanística Ainmo",
        "line_items[0][price_data][product_data][description]": (
            "PDF del predio seleccionado, con resultados, estados, fuentes y trazabilidad."
        ),
    }
    for key, value in metadata.items():
        data[f"metadata[{key}]"] = value
    session = await _stripe_request("POST", "/checkout/sessions", data=data)
    if not session.get("url") or not session.get("id"):
        raise PaymentError("Stripe no devolvió un enlace de pago.")
    return {"id": session["id"], "url": session["url"]}


async def create_subscription_checkout(*, user: dict[str, Any], plan: str) -> dict[str, Any]:
    """Create a Stripe-hosted monthly subscription checkout for an Ainmo user."""
    if not enabled():
        raise PaymentError("Los cobros no están habilitados durante la beta abierta.")
    selected = SUBSCRIPTION_PLANS.get(plan)
    if not selected or not selected["price_id"]:
        raise PaymentError("Este plan todavía no está habilitado para compra.")
    user_id = str(user.get("id") or "").strip()
    email = str(user.get("email") or "").strip()
    if not user_id or not email:
        raise PaymentError("La cuenta no tiene una identidad de facturación válida.")

    data: dict[str, Any] = {
        "mode": "subscription",
        "locale": "es",
        "success_url": f"{_app_url()}/settings?billing=success&session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{_app_url()}/settings?billing=cancelled",
        "line_items[0][quantity]": "1",
        "line_items[0][price]": selected["price_id"],
        "client_reference_id": user_id,
        "allow_promotion_codes": "true",
        "metadata[product]": "ainmo_subscription",
        "metadata[user_id]": user_id,
        "metadata[plan]": plan,
        "subscription_data[metadata][product]": "ainmo_subscription",
        "subscription_data[metadata][user_id]": user_id,
        "subscription_data[metadata][plan]": plan,
    }
    customer_id = str(user.get("stripe_customer_id") or "").strip()
    if customer_id:
        data["customer"] = customer_id
    else:
        data["customer_email"] = email
    session = await _stripe_request("POST", "/checkout/sessions", data=data)
    if not session.get("url") or not session.get("id"):
        raise PaymentError("Stripe no devolvió un enlace de suscripción.")
    return {"id": session["id"], "url": session["url"]}


async def create_billing_portal(customer_id: str) -> dict[str, Any]:
    if not enabled():
        raise PaymentError("Los cobros no están habilitados durante la beta abierta.")
    customer_id = str(customer_id or "").strip()
    if not customer_id.startswith("cus_"):
        raise PaymentError("La cuenta todavía no tiene una suscripción administrable.")
    session = await _stripe_request("POST", "/billing_portal/sessions", data={
        "customer": customer_id,
        "return_url": f"{_app_url()}/settings",
    })
    if not session.get("url"):
        raise PaymentError("Stripe no devolvió el portal de facturación.")
    return {"url": session["url"]}


async def get_checkout_session(session_id: str) -> dict[str, Any]:
    if not str(session_id).startswith("cs_"):
        raise PaymentError("Identificador de pago inválido.")
    return await _stripe_request("GET", f"/checkout/sessions/{session_id}")


def verify_webhook(payload: bytes, signature_header: str) -> dict[str, Any]:
    """Verify and decode a Stripe webhook without depending on stripe-python."""
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise PaymentError("El webhook de Stripe no está configurado.")
    timestamp = ""
    signatures: list[str] = []
    for part in str(signature_header or "").split(","):
        key, _, value = part.partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)
    if not timestamp or not signatures:
        raise PaymentError("Firma de Stripe incompleta.")
    try:
        signed_at = int(timestamp)
    except ValueError as exc:
        raise PaymentError("Fecha de firma de Stripe inválida.") from exc
    if abs(int(time.time()) - signed_at) > 300:
        raise PaymentError("La firma de Stripe expiró.")
    signed_payload = timestamp.encode() + b"." + payload
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, supplied) for supplied in signatures):
        raise PaymentError("Firma de Stripe inválida.")
    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaymentError("El webhook de Stripe no contiene JSON válido.") from exc
    if not isinstance(event, dict) or not event.get("type"):
        raise PaymentError("Evento de Stripe inválido.")
    return event


def _to_float(metadata: dict[str, str], key: str) -> float | None:
    value = metadata.get(key)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise PaymentError(f"El pago no conserva un valor válido para {key}.") from exc


async def paid_pdf_context(session_id: str) -> dict[str, Any]:
    if not session_id.startswith("cs_"):
        raise PaymentError("Identificador de pago inválido.")
    session = await _stripe_request("GET", f"/checkout/sessions/{session_id}")
    metadata = session.get("metadata") or {}
    if metadata.get("product") != "ainmo_pdf_report":
        raise PaymentError("Este pago no corresponde a un informe Ainmo.")
    if session.get("payment_status") != "paid":
        raise PaymentError("El pago del informe aún no está confirmado.")
    if str(session.get("currency", "")).lower() != "cop":
        raise PaymentError("La moneda del pago no coincide con el informe.")
    if int(session.get("amount_total") or -1) != _minor_units(PRICE_COP):
        raise PaymentError("El valor pagado no coincide con el precio vigente del informe.")

    lat = _to_float(metadata, "lat")
    lng = _to_float(metadata, "lng")
    if lat is None or lng is None or not metadata.get("expected_lotcodigo"):
        raise PaymentError("El pago no conserva la identidad completa del predio.")
    return {
        "lat": lat,
        "lng": lng,
        "vis_en_sitio": metadata.get("vis_en_sitio") == "true",
        "anu_m2": _to_float(metadata, "anu_m2"),
        "frente_m": _to_float(metadata, "frente_m"),
        "ancho_via_m": _to_float(metadata, "ancho_via_m"),
        "address": metadata.get("address", ""),
        "searched_address": metadata.get("searched_address", ""),
        "resolved_address": metadata.get("resolved_address", ""),
        "searched_chip": metadata.get("searched_chip", ""),
        "near_match": metadata.get("near_match") == "true",
        "search_mode": metadata.get("search_mode", ""),
        "expected_lotcodigo": metadata["expected_lotcodigo"],
    }


async def checkout_status(session_id: str) -> dict[str, Any]:
    context = await paid_pdf_context(session_id)
    return {
        "paid": True,
        "lotcodigo": context["expected_lotcodigo"],
        "address": context.get("resolved_address") or context.get("address") or "Predio consultado",
    }
