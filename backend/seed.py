"""Seed delivery rules (keyed by Shopify SKU) + license keys, and print a
signed sample checkout URL so you can exercise the flow without Shopify.

Run from backend/:  python seed.py
"""
import base64
import json
import random
import string

from app.auth import hash_password
from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import Admin, DeliveryType, LicenseKey, Product

settings = get_settings()


def rand_key(prefix: str) -> str:
    block = lambda: "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"{prefix}-{block()}-{block()}-{block()}"


# Sample Shopify SKUs -> how this app fulfills them.
RULES = [
    dict(sku="TKP-LIFETIME", slug="pro-license", name="Toolkit Pro — Lifetime License",
         description="Lifetime license for the desktop toolkit. One key per device.",
         delivery_type=DeliveryType.license_key),
    dict(sku="UIKIT-FIGMA", slug="ui-kit", name="UI Kit — Figma + Code",
         description="200+ components, downloadable Figma file and React export.",
         delivery_type=DeliveryType.file_download,
         download_url="https://example.com/files/ui-kit.zip"),
    dict(sku="COURSE-FULL", slug="course-access", name="Course — Full Access",
         description="Lifetime access to the video course and community.",
         delivery_type=DeliveryType.access_grant,
         access_template="Your access is active for {email}.\nLog in at https://example.com/learn using this email. Order ref: {order}"),
]


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not db.query(Admin).filter(Admin.email == settings.admin_email).first():
            db.add(Admin(email=settings.admin_email, password_hash=hash_password(settings.admin_password), name="Owner"))

        if db.query(Product).count() == 0:
            rules = [Product(**r) for r in RULES]
            db.add_all(rules)
            db.flush()
            key_rule = next(r for r in rules if r.delivery_type == DeliveryType.license_key)
            for _ in range(25):
                db.add(LicenseKey(product_id=key_rule.id, key_value=rand_key("TKP")))
            print(f"seeded {len(rules)} delivery rules + 25 license keys")

        db.commit()
        print("done. admin:", settings.admin_email, "/", settings.admin_password)

        # Build a sample cart (same `items` shape the Shopify theme forwards) so
        # you can test the checkout page immediately, without Shopify.
        items = [
            {"id": "111", "sku": "TKP-LIFETIME", "name": "Toolkit Pro — Lifetime License",
             "variant": None, "quantity": 1, "unitPrice": 49.0, "image": None},
            {"id": "222", "sku": "COURSE-FULL", "name": "Course — Full Access",
             "variant": None, "quantity": 1, "unitPrice": 99.0, "image": None},
        ]
        raw = json.dumps(items).encode("utf-8")
        items_b64 = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        print("\nSample checkout URL (paste in a browser):")
        print(f"  {settings.base_url}/checkout.html?items={items_b64}&source=seed&country=US&storename=Demo+Store")
    finally:
        db.close()


if __name__ == "__main__":
    main()
