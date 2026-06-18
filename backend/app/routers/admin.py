from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import create_access_token, require_admin, verify_password
from ..database import get_db
from ..delivery import fulfill_order
from ..models import (
    Admin, Customer, Delivery, LicenseKey, Order, OrderStatus, Product
)
from ..schemas import (
    KeysAddIn, OrderOut, ProductCreate, ProductOut, ProductUpdate, StatsOut, TokenOut
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/login", response_model=TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # OAuth2 form uses 'username' field; we treat it as email
    admin = db.query(Admin).filter(Admin.email == form.username).first()
    if not admin or not verify_password(form.password, admin.password_hash):
        raise HTTPException(401, "Invalid credentials")
    return TokenOut(access_token=create_access_token(admin.email), name=admin.name)


# ---------- Products ----------
@router.get("/products", response_model=list[ProductOut])
def all_products(db: Session = Depends(get_db), _: Admin = Depends(require_admin)):
    return db.query(Product).order_by(Product.created_at.desc()).all()


@router.post("/products", response_model=ProductOut)
def create_product(body: ProductCreate, db: Session = Depends(get_db), _: Admin = Depends(require_admin)):
    if db.query(Product).filter(Product.sku == body.sku).first():
        raise HTTPException(400, "A rule for this SKU already exists")
    product = Product(**body.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.patch("/products/{product_id}", response_model=ProductOut)
def update_product(product_id: int, body: ProductUpdate, db: Session = Depends(get_db), _: Admin = Depends(require_admin)):
    product = db.query(Product).get(product_id)
    if not product:
        raise HTTPException(404, "Not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return product


@router.delete("/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db), _: Admin = Depends(require_admin)):
    product = db.query(Product).get(product_id)
    if not product:
        raise HTTPException(404, "Not found")
    db.delete(product)
    db.commit()
    return {"ok": True}


@router.post("/products/{product_id}/keys", response_model=ProductOut)
def add_keys(product_id: int, body: KeysAddIn, db: Session = Depends(get_db), _: Admin = Depends(require_admin)):
    product = db.query(Product).get(product_id)
    if not product:
        raise HTTPException(404, "Not found")
    for raw in body.keys:
        raw = raw.strip()
        if raw:
            db.add(LicenseKey(product_id=product.id, key_value=raw))
    db.commit()
    db.refresh(product)
    return product


# ---------- Orders ----------
@router.get("/orders", response_model=list[OrderOut])
def all_orders(status: str | None = None, db: Session = Depends(get_db), _: Admin = Depends(require_admin)):
    q = db.query(Order).order_by(Order.created_at.desc())
    if status:
        q = q.filter(Order.status == status)
    return q.limit(500).all()


@router.post("/orders/{public_id}/fulfill", response_model=OrderOut)
def manual_fulfill(public_id: str, db: Session = Depends(get_db), _: Admin = Depends(require_admin)):
    order = db.query(Order).filter(Order.public_id == public_id).first()
    if not order:
        raise HTTPException(404, "Not found")
    if order.status == OrderStatus.pending:
        order.status = OrderStatus.paid
        order.paid_at = datetime.utcnow()
        db.commit()
    if order.status == OrderStatus.paid:
        fulfill_order(db, order)
    db.refresh(order)
    return order


@router.post("/orders/{public_id}/refund", response_model=OrderOut)
def refund(public_id: str, db: Session = Depends(get_db), _: Admin = Depends(require_admin)):
    order = db.query(Order).filter(Order.public_id == public_id).first()
    if not order:
        raise HTTPException(404, "Not found")
    order.status = OrderStatus.refunded
    db.commit()
    db.refresh(order)
    return order


# ---------- Stats ----------
@router.get("/stats", response_model=StatsOut)
def stats(db: Session = Depends(get_db), _: Admin = Depends(require_admin)):
    paid_states = (OrderStatus.paid, OrderStatus.delivered)
    revenue = db.query(func.coalesce(func.sum(Order.total_cents), 0)).filter(Order.status.in_(paid_states)).scalar() or 0
    orders_total = db.query(func.count(Order.id)).scalar() or 0
    orders_paid = db.query(func.count(Order.id)).filter(Order.status.in_(paid_states)).scalar() or 0
    orders_pending = db.query(func.count(Order.id)).filter(Order.status == OrderStatus.pending).scalar() or 0
    customers = db.query(func.count(Customer.id)).scalar() or 0
    products_active = db.query(func.count(Product.id)).filter(Product.active.is_(True)).scalar() or 0

    # revenue by day (last 14)
    since = datetime.utcnow() - timedelta(days=13)
    rows = (
        db.query(Order.paid_at, Order.total_cents)
        .filter(Order.status.in_(paid_states), Order.paid_at.isnot(None), Order.paid_at >= since)
        .all()
    )
    buckets = defaultdict(int)
    for paid_at, cents in rows:
        buckets[paid_at.strftime("%Y-%m-%d")] += cents
    revenue_by_day = []
    for i in range(13, -1, -1):
        day = (datetime.utcnow() - timedelta(days=i)).strftime("%Y-%m-%d")
        revenue_by_day.append({"date": day, "cents": buckets.get(day, 0)})

    # top products by units delivered
    top = (
        db.query(Delivery.product_name, func.count(Delivery.id).label("units"))
        .group_by(Delivery.product_name)
        .order_by(func.count(Delivery.id).desc())
        .limit(5)
        .all()
    )
    top_products = [{"name": name, "units": units} for name, units in top]

    return StatsOut(
        revenue_cents=revenue,
        orders_total=orders_total,
        orders_paid=orders_paid,
        orders_pending=orders_pending,
        customers=customers,
        products_active=products_active,
        revenue_by_day=revenue_by_day,
        top_products=top_products,
    )
