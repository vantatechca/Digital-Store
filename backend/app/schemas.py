from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field

from .models import DeliveryType, OrderStatus, PaymentStatus


# ---------- Auth ----------
class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    name: str


# ---------- Delivery rules (a.k.a. products) ----------
class ProductBase(BaseModel):
    sku: str                       # Shopify variant SKU this rule fulfills
    name: str
    description: str = ""
    delivery_type: DeliveryType = DeliveryType.license_key
    download_url: str = ""
    access_template: str = ""
    active: bool = True


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    sku: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    delivery_type: Optional[DeliveryType] = None
    download_url: Optional[str] = None
    access_template: Optional[str] = None
    active: Optional[bool] = None


class ProductOut(ProductBase):
    id: int
    keys_available: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


class KeysAddIn(BaseModel):
    keys: List[str]


# ---------- Checkout ----------
# A line item as the Shopify theme forwards it (base64 `items` payload).
class CheckoutItemIn(BaseModel):
    sku: str = ""                                  # Shopify variant SKU (may be blank)
    id: str = ""                                   # Shopify variant id (informational)
    name: str
    variant: Optional[str] = None                  # variant title, e.g. "1g"
    quantity: int = Field(default=1, ge=1, le=100)
    unitPrice: float = Field(ge=0)                 # price per unit, in the store currency (dollars)


class CheckoutIn(BaseModel):
    items: List[CheckoutItemIn]
    email: EmailStr
    name: str = ""
    payment_method: str = "test"                   # test | stripe | crypto
    currency: str = "USD"
    # Metadata forwarded from the storefront (recorded with the payment).
    storename: str = ""
    source: str = ""
    discount: str = ""


# Buyer clicked Pay → we record a PENDING order before handing off to Whop.
class PendingOrderIn(BaseModel):
    items: List[CheckoutItemIn]
    email: EmailStr
    name: str = ""
    currency: str = "USD"
    order_ref: str                                 # ties the order to the Whop session
    whop_session_id: str = ""
    storename: str = ""
    source: str = ""
    discount: str = ""


class DeliveryOut(BaseModel):
    product_name: str
    delivery_type: DeliveryType
    payload: str
    download_url: Optional[str] = None

    class Config:
        from_attributes = True


class CheckoutOut(BaseModel):
    order_public_id: str
    status: OrderStatus
    total_cents: int
    currency: str
    payment_method: str
    # populated when payment confirmed instantly (test provider)
    deliveries: List[DeliveryOut] = []
    # populated when an external redirect/invoice is required
    payment_url: Optional[str] = None


# ---------- Orders (admin) ----------
class OrderItemOut(BaseModel):
    product_name: str
    unit_price_cents: int
    quantity: int

    class Config:
        from_attributes = True


class OrderOut(BaseModel):
    public_id: str
    email: str
    total_cents: int
    currency: str
    status: OrderStatus
    payment_method: str
    created_at: datetime
    paid_at: Optional[datetime] = None
    items: List[OrderItemOut] = []

    class Config:
        from_attributes = True


class StatsOut(BaseModel):
    revenue_cents: int
    orders_total: int
    orders_paid: int
    orders_pending: int
    customers: int
    products_active: int
    revenue_by_day: List[dict] = []
    top_products: List[dict] = []
