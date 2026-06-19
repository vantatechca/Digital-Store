import enum
import json
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey, Integer, String, Text
)
from sqlalchemy.orm import relationship

from .database import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class DeliveryType(str, enum.Enum):
    license_key = "license_key"   # hand out a key from the product's key pool
    file_download = "file_download"  # tokenized, expiring download link
    access_grant = "access_grant"    # email + credentials / membership note


class OrderStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    delivered = "delivered"
    refunded = "refunded"
    cancelled = "cancelled"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    failed = "failed"


class Admin(Base):
    __tablename__ = "admins"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(120), default="Admin")
    created_at = Column(DateTime, default=datetime.utcnow)


class Product(Base):
    """A delivery rule for a Shopify product/variant.

    Shopify owns the product catalogue and pricing; this row only holds the
    *fulfillment* config (delivery type + license keys / download URL / access
    template), matched to an incoming Shopify line item by `sku`.
    """
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    # Shopify variant SKU — the key incoming cart line items are matched on.
    sku = Column(String(160), unique=True, nullable=False, index=True)
    slug = Column(String(160), unique=True, nullable=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    # Pricing lives in Shopify; kept only for admin reference, not used at checkout.
    price_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String(8), default="USD")
    image_url = Column(String(500), default="")
    delivery_type = Column(Enum(DeliveryType), default=DeliveryType.license_key)
    # for file_download: the file URL/path served on delivery
    download_url = Column(String(500), default="")
    # for access_grant: template text emailed to buyer (supports {order} / {email})
    access_template = Column(Text, default="")
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    license_keys = relationship("LicenseKey", back_populates="product", cascade="all, delete-orphan")

    @property
    def keys_available(self) -> int:
        return sum(1 for k in self.license_keys if not k.is_used)


class LicenseKey(Base):
    __tablename__ = "license_keys"
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    key_value = Column(String(255), nullable=False)
    is_used = Column(Boolean, default=False)
    assigned_order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("Product", back_populates="license_keys")


class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(160), default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    orders = relationship("Order", back_populates="customer")


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    public_id = Column(String(32), unique=True, default=_uuid, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    email = Column(String(255), nullable=False)  # denormalized for fast lookup
    total_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String(8), default="USD")
    status = Column(Enum(OrderStatus), default=OrderStatus.pending, index=True)
    payment_method = Column(String(40), default="")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    paid_at = Column(DateTime, nullable=True)

    customer = relationship("Customer", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    payment = relationship("Payment", back_populates="order", uselist=False, cascade="all, delete-orphan")
    deliveries = relationship("Delivery", back_populates="order", cascade="all, delete-orphan")

    @property
    def store(self) -> str:
        """Storefront name, recorded in the payment metadata at checkout."""
        try:
            if self.payment and self.payment.raw:
                return json.loads(self.payment.raw).get("storename") or ""
        except Exception:
            pass
        return ""


class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    # Shopify SKU for the line; product_id is the matched local delivery rule
    # (null when no rule exists yet — order is still recorded for manual handling).
    sku = Column(String(160), default="", index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    product_name = Column(String(200), nullable=False)
    unit_price_cents = Column(Integer, nullable=False)
    quantity = Column(Integer, default=1)

    order = relationship("Order", back_populates="items")
    product = relationship("Product")


class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    provider = Column(String(40), default="test")   # stripe | crypto | test
    provider_ref = Column(String(255), default="")  # external charge / invoice id
    amount_cents = Column(Integer, nullable=False)
    status = Column(Enum(PaymentStatus), default=PaymentStatus.pending)
    raw = Column(Text, default="")                  # JSON blob of provider payload
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("Order", back_populates="payment")


class Delivery(Base):
    __tablename__ = "deliveries"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    product_name = Column(String(200), nullable=False)
    delivery_type = Column(Enum(DeliveryType), nullable=False)
    # payload shown to buyer: a license key, a download token URL, or access text
    payload = Column(Text, default="")
    download_token = Column(String(64), default="", index=True)
    download_count = Column(Integer, default=0)
    download_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("Order", back_populates="deliveries")
