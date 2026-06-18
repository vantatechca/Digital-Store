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
from ..schemas import CheckoutIn, CheckoutOut, DeliveryOut

settings = get_settings()
router = APIRouter(prefix="/api", tags=["checkout"])


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
