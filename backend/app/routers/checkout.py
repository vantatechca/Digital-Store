import base64
import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..delivery import fulfill_order
from ..models import (
    Customer, Order, OrderItem, OrderStatus, Payment, PaymentStatus, Product
)
from ..payments import create_payment
from ..schemas import CheckoutIn, CheckoutOut, DeliveryOut, PendingOrderIn

settings = get_settings()
router = APIRouter(prefix="/api", tags=["checkout"])


def _build_order_items(db: Session, order: Order, items) -> int:
    """Add OrderItems to an order from cart items; returns the total in cents."""
    total = 0
    for ci in items:
        cents = round(ci.unitPrice * 100)
        rule = db.query(Product).filter(Product.sku == ci.sku).first() if ci.sku else None
        line_name = f"{ci.name} — {ci.variant}" if ci.variant else ci.name
        total += cents * ci.quantity
        db.add(OrderItem(
            order_id=order.id,
            sku=ci.sku,
            product_id=rule.id if rule else None,
            product_name=line_name,
            unit_price_cents=cents,
            quantity=ci.quantity,
        ))
    return total


@router.post("/checkout", response_model=CheckoutOut)
def checkout(payload: CheckoutIn, db: Session = Depends(get_db)):
    if not payload.items:
        raise HTTPException(400, "Cart is empty")

    currency = payload.currency or "USD"

    # upsert customer
    customer = db.query(Customer).filter(Customer.email == payload.email).first()
    if not customer:
        customer = Customer(email=payload.email, name=payload.name)
        db.add(customer)
        db.flush()

    order = Order(
        customer_id=customer.id,
        email=payload.email,
        currency=currency,
        payment_method=payload.payment_method,
        status=OrderStatus.pending,
    )
    db.add(order)
    db.flush()

    total = 0
    for ci in payload.items:
        # Price + name come from the Shopify storefront. The local rule, if any,
        # only decides how the item is fulfilled — match it by SKU.
        cents = round(ci.unitPrice * 100)
        rule = db.query(Product).filter(Product.sku == ci.sku).first() if ci.sku else None
        line_name = f"{ci.name} — {ci.variant}" if ci.variant else ci.name
        total += cents * ci.quantity
        db.add(OrderItem(
            order_id=order.id,
            sku=ci.sku,
            product_id=rule.id if rule else None,
            product_name=line_name,
            unit_price_cents=cents,
            quantity=ci.quantity,
        ))

    order.total_cents = total
    order.currency = currency

    success_url = f"{settings.base_url}/checkout.html?order={order.public_id}"
    cancel_url = f"{settings.base_url}/checkout.html?cancelled={order.public_id}"

    result = create_payment(payload.payment_method, order, success_url, cancel_url)

    raw = {
        "storename": payload.storename,
        "source": payload.source,
        "discount": payload.discount,
        "provider": result.get("raw", {}),
    }
    payment = Payment(
        order_id=order.id,
        provider=payload.payment_method,
        provider_ref=result.get("provider_ref", ""),
        amount_cents=total,
        status=PaymentStatus.confirmed if result["status"] == "confirmed" else PaymentStatus.pending,
        raw=json.dumps(raw)[:5000],
    )
    db.add(payment)

    deliveries_out: list[DeliveryOut] = []
    if result["status"] == "confirmed":
        order.status = OrderStatus.paid
        from datetime import datetime
        order.paid_at = datetime.utcnow()
        db.commit()
        db.refresh(order)
        deliveries = fulfill_order(db, order)
        deliveries_out = [
            DeliveryOut(
                product_name=d.product_name,
                delivery_type=d.delivery_type,
                payload=d.payload,
            )
            for d in deliveries
        ]
    else:
        db.commit()
        db.refresh(order)

    return CheckoutOut(
        order_public_id=order.public_id,
        status=order.status,
        total_cents=order.total_cents,
        currency=order.currency,
        payment_method=order.payment_method,
        deliveries=deliveries_out,
        payment_url=result.get("payment_url"),
    )


@router.post("/orders/create-pending")
def create_pending_order(payload: PendingOrderIn, db: Session = Depends(get_db)):
    """Buyer clicked Pay → record a PENDING Whop order before handing off.

    The order shows in admin immediately. It flips to paid either via the Whop
    webhook (matched on order_ref) or the admin's manual "Mark paid" button.
    """
    if not payload.items:
        raise HTTPException(400, "Cart is empty")

    # Idempotent: a repeated Pay click for the same session reuses the order.
    existing = db.query(Payment).filter(Payment.provider_ref == payload.order_ref).first()
    if existing and existing.order:
        return {"order_public_id": existing.order.public_id, "status": existing.order.status}

    currency = payload.currency or "USD"
    customer = db.query(Customer).filter(Customer.email == payload.email).first()
    if not customer:
        customer = Customer(email=payload.email, name=payload.name)
        db.add(customer)
        db.flush()

    order = Order(
        customer_id=customer.id,
        email=payload.email,
        currency=currency,
        payment_method="whop",
        status=OrderStatus.pending,
    )
    db.add(order)
    db.flush()

    total = _build_order_items(db, order, payload.items)
    order.total_cents = total
    order.currency = currency

    raw = {
        "storename": payload.storename,
        "source": payload.source,
        "discount": payload.discount,
        "whop_session_id": payload.whop_session_id,
    }
    db.add(Payment(
        order_id=order.id,
        provider="whop",
        provider_ref=payload.order_ref,   # links the Whop session/webhook to this order
        amount_cents=total,
        status=PaymentStatus.pending,
        raw=json.dumps(raw)[:5000],
    ))
    db.commit()
    db.refresh(order)
    return {"order_public_id": order.public_id, "status": order.status}


def _confirm_and_fulfill(db: Session, order: Order, provider_ref: str = "") -> None:
    if order.status in (OrderStatus.paid, OrderStatus.delivered):
        return
    from datetime import datetime
    order.status = OrderStatus.paid
    order.paid_at = datetime.utcnow()
    if order.payment:
        order.payment.status = PaymentStatus.confirmed
        if provider_ref:
            order.payment.provider_ref = provider_ref
    db.commit()
    db.refresh(order)
    fulfill_order(db, order)


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    # NOTE: verify Stripe-Signature with settings.stripe_webhook_secret in production.
    event = await request.json()
    if event.get("type") == "checkout.session.completed":
        session = event["data"]["object"]
        public_id = session.get("client_reference_id")
        order = db.query(Order).filter(Order.public_id == public_id).first()
        if order:
            _confirm_and_fulfill(db, order, session.get("payment_intent", ""))
    return {"ok": True}


@router.post("/webhooks/crypto")
async def crypto_webhook(request: Request, db: Session = Depends(get_db)):
    # NOTE: verify NowPayments IPN HMAC with settings.nowpayments_ipn_secret in production.
    body = await request.json()
    if body.get("payment_status") in ("finished", "confirmed"):
        public_id = body.get("order_id")
        order = db.query(Order).filter(Order.public_id == public_id).first()
        if order:
            _confirm_and_fulfill(db, order, str(body.get("payment_id", "")))
    return {"ok": True}


def _verify_whop_signature(secret: str, headers, body: bytes) -> bool:
    """Standard Webhooks HMAC-SHA256 verification. Accepts unsigned in dev (no secret)."""
    if not secret:
        return True
    wid = headers.get("webhook-id", "")
    wts = headers.get("webhook-timestamp", "")
    wsig = headers.get("webhook-signature", "")
    if not (wid and wts and wsig):
        return False
    # Whop secrets come prefixed ("whsec_<base64>" svix-style, or "ws_<hex>").
    # We don't know the exact byte encoding, so try the valid interpretations.
    part = secret.split("_", 1)[1] if "_" in secret else secret
    key_candidates = []
    for derive in (lambda p: base64.b64decode(p), lambda p: bytes.fromhex(p)):
        try:
            key_candidates.append(derive(part))
        except Exception:
            pass
    key_candidates.append(part.encode())
    key_candidates.append(secret.encode())

    signed = f"{wid}.{wts}.".encode() + body
    sigs = [p.split(",", 1)[1] if "," in p else p for p in wsig.split(" ")]
    for key_bytes in key_candidates:
        expected = base64.b64encode(hmac.new(key_bytes, signed, hashlib.sha256).digest()).decode()
        for sig in sigs:                      # header may hold several "v1,<sig>" entries
            if hmac.compare_digest(sig, expected):
                return True
    return False


def _find_order_ref(obj):
    """Recursively locate our 'order_ref' anywhere in the webhook payload."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "order_ref" and isinstance(v, str):
                return v
            found = _find_order_ref(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_order_ref(v)
            if found:
                return found
    return None


@router.post("/webhooks/whop")
async def whop_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    if not _verify_whop_signature(settings.whop_webhook_secret, request.headers, body):
        raise HTTPException(401, "Invalid webhook signature")

    try:
        event = json.loads(body)
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    action = str(event.get("action") or event.get("type") or event.get("event") or "")
    order_ref = _find_order_ref(event)
    # Logged so we can confirm the real payload shape and tighten matching.
    print(f"[Whop webhook] action={action} order_ref={order_ref} body={body[:1500]!r}")

    is_success = any(s in action.lower() for s in ("succ", "valid", "complete", "paid"))
    if is_success and order_ref:
        payment = db.query(Payment).filter(Payment.provider_ref == order_ref).first()
        if payment and payment.order:
            _confirm_and_fulfill(db, payment.order)
    return {"ok": True}
