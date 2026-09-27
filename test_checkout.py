import asyncio

from fastapi.testclient import TestClient

import payments
from api import app


client = TestClient(app)


def test_public_config_only_enables_checkout_with_stripe_key(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json()["pdf_checkout_enabled"] is False
    assert response.json()["pdf_report_price_cop"] == 99000

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_example")
    response = client.get("/api/config")
    assert response.json()["pdf_checkout_enabled"] is True


def test_checkout_uses_cop_minor_units_and_binds_report_metadata(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_example")
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
