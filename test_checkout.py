import asyncio
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

import payments
from api import app


client = TestClient(app)


def test_public_config_requires_explicit_launch_flag(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("AINMO_BILLING_ENABLED", raising=False)
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json()["pdf_checkout_enabled"] is False
    assert response.json()["pdf_report_price_cop"] == 99000

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_example")
    response = client.get("/api/config")
    assert response.json()["stripe_configured"] is True
    assert response.json()["pdf_checkout_enabled"] is False

    monkeypatch.setenv("AINMO_BILLING_ENABLED", "true")
    response = client.get("/api/config")
    assert response.json()["open_beta"] is True
    assert response.json()["pdf_checkout_enabled"] is False

    monkeypatch.setenv("AINMO_OPEN_BETA", "false")
    response = client.get("/api/config")
    assert response.json()["pdf_checkout_enabled"] is True


def test_checkout_uses_cop_minor_units_and_binds_report_metadata(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_example")
    monkeypatch.setenv("AINMO_BILLING_ENABLED", "true")
    captured = {}

    async def fake_request(method, path, *, data=None):
        captured.update({"method": method, "path": path, "data": data})
        return {"id": "cs_test_paid", "url": "https://checkout.stripe.test/session"}

    monkeypatch.setattr(payments, "_stripe_request", fake_request)
    result = asyncio.run(payments.create_pdf_checkout({
        "lat": 4.6679,
        "lng": -74.052,
        "expected_lotcodigo": "008310012021",
        "address": "CL 85 # 11-35",
    }))

    assert result["id"] == "cs_test_paid"
    assert captured["data"]["line_items[0][price_data][currency]"] == "cop"
    assert captured["data"]["line_items[0][price_data][unit_amount]"] == "9900000"
    assert captured["data"]["metadata[expected_lotcodigo]"] == "008310012021"
    assert captured["data"]["metadata[lat]"] == "4.6679"


def test_paid_session_reconstructs_server_bound_report_context(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_example")

    async def fake_request(method, path, *, data=None):
        assert method == "GET"
        assert path == "/checkout/sessions/cs_test_paid"
        return {
            "payment_status": "paid",
            "currency": "cop",
            "amount_total": 9900000,
            "metadata": {
                "product": "ainmo_pdf_report",
                "lat": "4.6679",
                "lng": "-74.052",
                "expected_lotcodigo": "008310012021",
                "near_match": "false",
                "address": "CL 85 # 11-35",
            },
        }

    monkeypatch.setattr(payments, "_stripe_request", fake_request)
    context = asyncio.run(payments.paid_pdf_context("cs_test_paid"))
    assert context["lat"] == 4.6679
    assert context["lng"] == -74.052
    assert context["expected_lotcodigo"] == "008310012021"
    assert context["address"] == "CL 85 # 11-35"


def test_unpaid_session_never_unlocks_pdf(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_example")

    async def fake_request(method, path, *, data=None):
        return {
            "payment_status": "unpaid",
            "currency": "cop",
            "amount_total": 9900000,
            "metadata": {"product": "ainmo_pdf_report"},
        }

    monkeypatch.setattr(payments, "_stripe_request", fake_request)
    try:
        asyncio.run(payments.paid_pdf_context("cs_test_unpaid"))
    except payments.PaymentError as exc:
        assert "no está confirmado" in str(exc)
    else:
        raise AssertionError("An unpaid Stripe session unlocked the report")


def test_checkout_success_page_is_noindex():
    response = client.get("/checkout/success?session_id=cs_test_pending")
    assert response.status_code == 200
    assert response.headers["x-robots-tag"] == "noindex"
    assert "Confirmando su pago" in response.text


def test_subscription_checkout_binds_user_plan_and_uses_recurring_price(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_example")
    monkeypatch.setenv("AINMO_BILLING_ENABLED", "true")
    monkeypatch.setitem(payments.SUBSCRIPTION_PLANS["pro"], "price_id", "price_pro_monthly")
    captured = {}

    async def fake_request(method, path, *, data=None):
        captured.update({"method": method, "path": path, "data": data})
        return {"id": "cs_test_subscription", "url": "https://checkout.stripe.test/subscription"}

    monkeypatch.setattr(payments, "_stripe_request", fake_request)
    result = asyncio.run(payments.create_subscription_checkout(
        user={"id": "user-123", "email": "equipo@example.com"}, plan="pro",
    ))

    assert result["id"] == "cs_test_subscription"
    assert captured["data"]["mode"] == "subscription"
    assert captured["data"]["line_items[0][price]"] == "price_pro_monthly"
    assert captured["data"]["metadata[user_id]"] == "user-123"
    assert captured["data"]["metadata[plan]"] == "pro"
    assert captured["data"]["subscription_data[metadata][user_id]"] == "user-123"


def test_stripe_webhook_signature_is_verified(monkeypatch):
    secret = "whsec_test"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
    payload = json.dumps({"id": "evt_1", "type": "customer.subscription.updated", "data": {"object": {}}}).encode()
    timestamp = int(time.time())
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()

    event = payments.verify_webhook(payload, f"t={timestamp},v1={digest}")

    assert event["id"] == "evt_1"
    assert event["type"] == "customer.subscription.updated"
