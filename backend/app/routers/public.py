from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import Delivery, DeliveryType, Order, Product
from ..schemas import OrderLookupIn
from .. import storage

settings = get_settings()
router = APIRouter(prefix="/api", tags=["public"])


def _order_payload(order: Order) -> dict:
    return {
        "order_public_id": order.public_id,
        "status": order.status,
        "total_cents": order.total_cents,
        "currency": order.currency,
        "deliveries": [
            {"product_name": d.product_name, "delivery_type": d.delivery_type, "payload": d.payload}
            for d in order.deliveries
        ],
    }


@router.get("/orders/{public_id}")
def get_order(public_id: str, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.public_id == public_id).first()
    if not order:
        raise HTTPException(404, "Order not found")
    return _order_payload(order)


@router.post("/order-lookup")
def order_lookup(payload: OrderLookupIn, db: Session = Depends(get_db)):
    """Self-serve re-download — requires both the order id and the buyer's email."""
    order = db.query(Order).filter(Order.public_id == payload.order_id.strip()).first()
    if not order or order.email.lower() != payload.email.strip().lower():
        raise HTTPException(404, "No order found for that order id + email")
    return _order_payload(order)


@router.get("/download/{token}")
def download(token: str, db: Session = Depends(get_db)):
    d = db.query(Delivery).filter(Delivery.download_token == token).first()
    if not d or d.delivery_type != DeliveryType.file_download:
        raise HTTPException(404, "Invalid download link")
    if d.download_expires_at and d.download_expires_at < datetime.utcnow():
        raise HTTPException(410, "Download link expired")
    if d.download_count >= settings.max_downloads_per_item:
        raise HTTPException(429, "Download limit reached")

    product = db.query(Product).get(d.product_id)
    if not product or not product.download_url:
        raise HTTPException(404, "File unavailable")

    target = product.download_url

    # Object key → stream the file THROUGH the backend so the real storage URL
    # is never exposed to the browser (nothing to capture or reshare).
    if storage.is_object_key(target):
        if not storage.is_configured():
            raise HTTPException(503, "File storage not configured")
        try:
            obj = storage.get_object(target)
        except Exception:
            raise HTTPException(404, "File unavailable")
        d.download_count += 1
        db.commit()
        filename = target.rsplit("/", 1)[-1]
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        if obj.get("ContentLength"):
            headers["Content-Length"] = str(obj["ContentLength"])
        return StreamingResponse(
            obj["Body"].iter_chunks(chunk_size=64 * 1024),
            media_type=obj.get("ContentType") or "application/octet-stream",
            headers=headers,
        )

    # Plain public URL → redirect (the host owner chose to make it public).
    d.download_count += 1
    db.commit()
    return RedirectResponse(target)
