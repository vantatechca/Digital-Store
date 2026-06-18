from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import Admin

settings = get_settings()
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/admin/login", auto_error=False)
ALGORITHM = "HS256"


def hash_password(raw: str) -> str:
    return pwd_context.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return pwd_context.verify(raw, hashed)


def create_access_token(sub: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.access_token_ttl_minutes)
    return jwt.encode({"sub": sub, "exp": expire}, settings.secret_key, algorithm=ALGORITHM)


def require_admin(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> Admin:
    cred_err = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise cred_err
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        email = payload.get("sub")
    except JWTError:
        raise cred_err
    admin = db.query(Admin).filter(Admin.email == email).first()
    if not admin:
        raise cred_err
    return admin
