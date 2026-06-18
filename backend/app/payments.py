"""Payment provider abstraction.

Each provider returns a dict:
  { "status": "confirmed" | "pending", "provider_ref": str, "payment_url": str | None, "raw": dict }

- test:   auto-confirms (dev only). Order is delivered immediately.
- stripe: creates a Checkout Session; confirmation arrives via webhook.
- crypto: creates a NowPayments invoice; confirmation arrives via IPN webhook.

Real provider calls are written but guarded so the scaffold runs without keys.
"""
import httpx

from .config import get_settings

settings = get_settings()


def create_payment(provider: str, order, success_url: str, cancel_url: str) -> dict:
    if provider == "test":
        if not settings.enable_test_provider:
            raise ValueError("Test provider disabled")
        return {"status": "confirmed", "provider_ref": f"test_{order.public_id}", "payment_url": None, "raw": {}}

    if provider == "stripe":
        return _stripe_session(order, success_url, cancel_url)

    if provider == "crypto":
        return _nowpayments_invoice(order, success_url)

    raise ValueError(f"Unknown provider: {provider}")


def _stripe_session(order, success_url: str, cancel_url: str) -> dict:
    if not settings.stripe_secret_key:
        # graceful stub so the flow still demonstrates the pending path
        return {
            "status": "pending",
            "provider_ref": "",
            "payment_url": f"{settings.base_url}/checkout.html?stub_stripe={order.public_id}",
            "raw": {"stub": True},
        }
    data = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": order.public_id,
        "customer_email": order.email,
        "line_items[0][price_data][currency]": order.currency.lower(),
        "line_items[0][price_data][product_data][name]": f"Order {order.public_id}",
        "line_items[0][price_data][unit_amount]": str(order.total_cents),
        "line_items[0][quantity]": "1",
    }
    r = httpx.post(
        "https://api.stripe.com/v1/checkout/sessions",
        data=data,
        auth=(settings.stripe_secret_key, ""),
        timeout=20,
    )
    r.raise_for_status()
    body = r.json()
    return {"status": "pending", "provider_ref": body["id"], "payment_url": body["url"], "raw": body}


def _nowpayments_invoice(order, success_url: str) -> dict:
    if not settings.nowpayments_api_key:
        return {
            "status": "pending",
            "provider_ref": "",
            "payment_url": f"{settings.base_url}/checkout.html?stub_crypto={order.public_id}",
            "raw": {"stub": True},
        }
    r = httpx.post(
        "https://api.nowpayments.io/v1/invoice",
        headers={"x-api-key": settings.nowpayments_api_key},
        json={
            "price_amount": round(order.total_cents / 100, 2),
            "price_currency": order.currency.lower(),
            "order_id": order.public_id,
            "success_url": success_url,
        },
        timeout=20,
    )
    r.raise_for_status()
    body = r.json()
    return {
        "status": "pending",
        "provider_ref": str(body.get("id", "")),
        "payment_url": body.get("invoice_url"),
        "raw": body,
    }
