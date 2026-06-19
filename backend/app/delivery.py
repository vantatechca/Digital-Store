"""Digital fulfillment: turn a paid order into delivered goods.

Three delivery types:
  - license_key   -> pull an unused key from the product's pool
  - file_download -> mint a tokenized, expiring, download-count-limited link
  - access_grant  -> render the product's access template
Then email the buyer a single confirmation with everything.
"""
import html
import json
import secrets
from datetime import datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Delivery, DeliveryType, LicenseKey, Order, OrderItem, OrderStatus, Product

settings = get_settings()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def fulfill_order(db: Session, order: Order) -> list[Delivery]:
    """Idempotent-ish: only fulfills a paid, not-yet-delivered order."""
    if order.status == OrderStatus.delivered:
        return order.deliveries
    if order.status != OrderStatus.paid:
        raise ValueError(f"Cannot fulfill order in status {order.status}")

    deliveries: list[Delivery] = []
    for item in order.items:
        # Match the Shopify SKU to a local delivery rule (may be None).
        rule = db.query(Product).filter(Product.sku == item.sku).first() if item.sku else None
        for _ in range(item.quantity):
            deliveries.append(_deliver_one(db, order, item, rule))

    order.status = OrderStatus.delivered
    db.commit()
    for d in deliveries:
        db.refresh(d)

    _send_confirmation_email(order, deliveries)
    return deliveries


def _deliver_one(db: Session, order: Order, item: OrderItem, product: Product | None) -> Delivery:
    # No delivery rule for this SKU: record it for manual handling by an admin.
    if product is None:
        d = Delivery(
            order_id=order.id,
            product_id=None,
            product_name=item.product_name,
            delivery_type=DeliveryType.access_grant,
            payload=f"No delivery rule for SKU '{item.sku}' — admin will deliver manually.",
        )
        db.add(d)
        return d

    d = Delivery(
        order_id=order.id,
        product_id=product.id,
        product_name=product.name,
        delivery_type=product.delivery_type,
    )

    if product.delivery_type == DeliveryType.license_key:
        key = (
            db.query(LicenseKey)
            .filter(LicenseKey.product_id == product.id, LicenseKey.is_used.is_(False))
            .with_for_update(skip_locked=True) if not settings.database_url.startswith("sqlite")
            else db.query(LicenseKey).filter(
                LicenseKey.product_id == product.id, LicenseKey.is_used.is_(False)
            )
        ).first()
        if key:
            key.is_used = True
            key.assigned_order_id = order.id
            db.flush()  # ensure this key is excluded from the next lookup in the same order
            d.payload = key.key_value
        else:
            d.payload = "OUT_OF_STOCK — admin will follow up"

    elif product.delivery_type == DeliveryType.file_download:
        d.download_token = _new_token()
        d.download_expires_at = datetime.utcnow() + timedelta(
            hours=settings.download_token_ttl_hours
        )
        d.payload = f"{settings.base_url}/api/download/{d.download_token}"

    elif product.delivery_type == DeliveryType.access_grant:
        tpl = product.access_template or "Access granted. Reply to this email for help."
        d.payload = tpl.replace("{order}", order.public_id).replace("{email}", order.email)

    db.add(d)
    return d


def _store_name(order: Order) -> str:
    """The storefront name recorded with the payment, used as the email From name."""
    try:
        if order.payment and order.payment.raw:
            name = (json.loads(order.payment.raw).get("storename") or "").strip()
            if name:
                return name
    except Exception:
        pass
    return settings.mail_from_name


def _email_html(order: Order, deliveries: list[Delivery], from_name: str) -> str:
    e = html.escape
    rows = []
    for d in deliveries:
        if d.delivery_type == DeliveryType.file_download:
            inner = (f'<a href="{e(d.payload)}" style="display:inline-block;background:#0f172a;color:#ffffff;'
                     f'text-decoration:none;padding:11px 20px;border-radius:8px;font-weight:600;font-size:14px">Download</a>')
        elif d.delivery_type == DeliveryType.license_key:
            inner = (f'<div style="font-family:Menlo,Consolas,monospace;background:#f6f7f9;border:1px dashed #cbd5e1;'
                     f'border-radius:8px;padding:11px 13px;font-size:14px;word-break:break-all;color:#0f172a">{e(d.payload)}</div>')
        else:
            inner = f'<div style="white-space:pre-wrap;font-size:14px;color:#334155">{e(d.payload)}</div>'
        rows.append(
            f'<tr><td style="padding:16px 0;border-bottom:1px solid #e7e9ee">'
            f'<div style="font-size:12px;color:#64748b;margin-bottom:8px">{e(d.product_name)}</div>{inner}</td></tr>'
        )
    lookup = f"{settings.base_url}/lookup.html"
    return f"""<!doctype html><html><body style="margin:0;background:#f6f7f9;font-family:Inter,Arial,sans-serif;color:#0f172a">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f7f9;padding:28px 12px">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border:1px solid #e7e9ee;border-radius:14px;overflow:hidden">
        <tr><td style="padding:24px 28px;border-bottom:1px solid #e7e9ee;font-weight:700;font-size:18px">{e(from_name)}</td></tr>
        <tr><td style="padding:28px">
          <div style="font-size:20px;font-weight:700;margin-bottom:6px">Your order is ready ✅</div>
          <div style="font-size:14px;color:#64748b;margin-bottom:8px">Order <span style="font-family:monospace">{e(order.public_id)}</span></div>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{''.join(rows)}</table>
          <div style="font-size:13px;color:#64748b;margin-top:20px">Keep this email — your links and keys are above.</div>
          <div style="font-size:13px;color:#64748b;margin-top:8px">Lost your download? <a href="{lookup}" style="color:#0f172a;font-weight:600">Find your order</a> anytime using this email and your order id.</div>
        </td></tr>
        <tr><td style="padding:16px 28px;border-top:1px solid #e7e9ee;font-size:12px;color:#94a3b8">{e(from_name)} · secure digital delivery</td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def _send_confirmation_email(order: Order, deliveries: list[Delivery]) -> None:
    from_name = _store_name(order)
    lines = []
    for d in deliveries:
        if d.delivery_type == DeliveryType.license_key:
            lines.append(f"• {d.product_name} — License key: {d.payload}")
        elif d.delivery_type == DeliveryType.file_download:
            lines.append(f"• {d.product_name} — Download: {d.payload}")
        else:
            lines.append(f"• {d.product_name}\n{d.payload}")
    body = (
        f"Thanks for your order {order.public_id}!\n\n"
        + "\n".join(lines)
        + "\n\nKeep this email — your links/keys are above."
        + f"\n\nLost your download? Re-download anytime at {settings.base_url}/lookup.html"
        + f"\nUse your email and order id: {order.public_id}"
    )

    if not settings.resend_api_key:
        print("\n----- [DEV EMAIL] -----")
        print(f"To: {order.email}\nFrom: {from_name} <{settings.mail_from}>\nSubject: Your {from_name} order {order.public_id}")
        print(body)
        print("-----------------------\n")
        return

    try:
        httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": f"{from_name} <{settings.mail_from}>",
                "to": [order.email],
                "subject": f"Your {from_name} order {order.public_id}",
                "text": body,
                "html": _email_html(order, deliveries, from_name),
            },
            timeout=15,
        )
    except Exception as exc:  # delivery already recorded; don't fail the request
        print(f"[email error] {exc}")
