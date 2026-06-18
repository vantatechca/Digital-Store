from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

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
