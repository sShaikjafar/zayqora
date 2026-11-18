import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import ACTIVE, Restaurant, User
from .security import decode_token

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = decode_token(creds.credentials)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Invalid or expired token")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "Invalid or expired token")
    if user.restaurant is not None and user.restaurant.status != ACTIVE:
        raise HTTPException(403, "This restaurant account is suspended")
    return user


def require_roles(*roles: str):
    """Authorization is enforced here, on the backend, for every protected route."""

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "You do not have permission to do this")
        return user

    return checker


def get_current_restaurant(user: User = Depends(get_current_user)) -> Restaurant:
    """Tenant scope comes from the authenticated user, never from client input."""
    if user.restaurant is None:
        raise HTTPException(403, "This account is not linked to a restaurant")
    return user.restaurant
