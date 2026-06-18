from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import Delivery, DeliveryType, Order, Product

settings = get_settings()
router = APIRouter(prefix="/api", tags=["public"])


@router.get("/orders/{public_id}")
def get_order(public_id: str, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.public_id == public_id).first()
    if not order:
        raise HTTPException(404, "Order not found")
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

    d.download_count += 1
    db.commit()
    return RedirectResponse(product.download_url)
