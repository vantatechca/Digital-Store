"""Server-trusted pricing.

Item prices arrive from the client (the Shopify-built `items` payload), so they
can't be trusted on their own — a buyer could edit the URL to pay 1¢. When a
delivery rule has an authoritative `price_cents` set, we charge THAT instead of
the client-supplied price. Products without a rule price fall back to the client
price (set a price on the rule to protect them).
"""
from sqlalchemy.orm import Session

from .models import Product


def authoritative_cents(db: Session, sku: str, client_unit_price) -> int:
    rule = db.query(Product).filter(Product.sku == sku).first() if sku else None
    if rule and rule.price_cents and rule.price_cents > 0:
        return rule.price_cents
    return round((client_unit_price or 0) * 100)
