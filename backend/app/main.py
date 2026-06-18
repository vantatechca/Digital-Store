from pathlib import Path
from typing import List # <-- Added for cart item lists
import os               # <-- Added to read env variables
import httpx            # <-- Added to communicate with Whop's servers

from fastapi import FastAPI, HTTPException # Added HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel # <-- Added for payload data structures

from .auth import hash_password
from .config import get_settings
from .database import Base, SessionLocal, engine
from .models import Admin
from .routers import admin, checkout, public

settings = get_settings()
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
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
    # Calculate the exact total on the backend to avoid price tampering
    total_amount = sum(item.unitPrice * item.quantity for item in payload.items)
    
    url = "https://api.whop.com/v1/checkout_configurations"
    headers = {
        "Authorization": f"Bearer {os.getenv('WHOP_API_KEY')}",
        "Content-Type": "application/json"
    }
    
    body = {
        "company_id": os.getenv("WHOP_COMPANY_ID"),
        "mode": "payment",
        "currency": payload.currency.lower(),
        "plan": {
            "initial_price": total_amount,
            "plan_type": "one_time"
        }
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=body, headers=headers)
            if response.status_code != 200:
                print(f"[Whop Error] Status: {response.status_code}, Body: {response.text}")
                raise HTTPException(status_code=400, detail="Whop gateway failed to initialize.")
            
            data = response.json()
            return {"sessionId": data.get("id")} # Returns the ch_xxxxxx session token
            
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


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")