from pathlib import Path
from typing import List # <-- For cart item lists
import uuid             # <-- To mint a reference linking the session to our order
import httpx            # <-- To communicate with Whop's servers

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .auth import hash_password
from .config import get_settings
from .database import Base, SessionLocal, engine
from .models import Admin
from .routers import admin, checkout, public

settings = get_settings()
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title=settings.app_name)

# ─── UPDATED CORS CONFIGURATION ───
# This tells the server to explicitly accept cross-origin requests from these domains
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "https://digital-store-frontend-0tk9.onrender.com"
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(public.router)
app.include_router(checkout.router)
app.include_router(admin.router)


# ─── WHOP SECURE SESSION SCHEMAS ───
class CartItem(BaseModel):
    name: str
    quantity: int
    unitPrice: float

class SessionPayload(BaseModel):
    items: List[CartItem]
    currency: str = "CAD"


# ─── NEW DYNAMIC PAYMENTS ROUTE ───
@app.post("/api/payments/create-whop-session")
async def create_whop_session(payload: SessionPayload):
    # Calculate the exact total on the backend to avoid price tampering.
    total_amount = round(sum(item.unitPrice * item.quantity for item in payload.items), 2)

    if not settings.whop_product_id or not settings.whop_api_key:
        raise HTTPException(status_code=500, detail="Whop is not configured (WHOP_API_KEY / WHOP_PRODUCT_ID).")

    # Define an inline one-time price on the product so a single product can
    # charge any cart total. `price` must be an object, not a bare number.
    url = f"{settings.whop_api_base}/api/v2/checkout_sessions"
    headers = {
        "Authorization": f"Bearer {settings.whop_api_key}",
        "Content-Type": "application/json",
    }
    # Reference that ties this Whop session to the order we create at pay-time.
    # It rides in the session metadata, so the webhook can find our order later.
    order_ref = uuid.uuid4().hex
    body = {
        "price": {
            "product_id": settings.whop_product_id,
            "initial_price": total_amount,
            "plan_type": "one_time",
            "currency": payload.currency.lower(),
        },
        "metadata": {
            "order_ref": order_ref,
            "currency": payload.currency,
            "total": total_amount,
            "items": "; ".join(f"{i.quantity}x {i.name}" for i in payload.items)[:480],
        },
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=body, headers=headers)
            if response.status_code not in (200, 201):
                print(f"[Whop Error] Status: {response.status_code}, Body: {response.text}")
                raise HTTPException(status_code=400, detail="Whop gateway failed to initialize.")

            data = response.json()
            session_id = data.get("id")
            if not session_id:
                print(f"[Whop Error] No session id in response: {data}")
                raise HTTPException(status_code=400, detail="Whop returned no session id.")
            return {"sessionId": session_id, "orderRef": order_ref}  # ch_xxxxxxxx

        except httpx.RequestError as exc:
            raise HTTPException(status_code=503, detail=f"Failed to reach billing server: {exc}")


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not db.query(Admin).filter(Admin.email == settings.admin_email).first():
            db.add(Admin(
                email=settings.admin_email,
                password_hash=hash_password(settings.admin_password),
                name="Owner",
            ))
            db.commit()
            print(f"[bootstrap] admin created: {settings.admin_email}")
    finally:
        db.close()


@app.get("/health")
def health():
    return {"ok": True, "app": settings.app_name}


# Serve the two static pages
@app.get("/")
def root():
    return RedirectResponse("/checkout.html")


@app.get("/checkout.html")
def checkout_page():
    return FileResponse(FRONTEND_DIR / "checkout.html")


@app.get("/admin.html")
def admin_page():
    return FileResponse(FRONTEND_DIR / "admin.html")


@app.get("/success.html")
def success_page():
    return FileResponse(FRONTEND_DIR / "success.html")


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")