import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import ACTIVE, OWNER, Restaurant, User
from ..ratelimit import limit
from ..schemas import LoginIn, RegisterRestaurantIn, TokenOut, UserOut
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

RESERVED_SLUGS = {"admin", "api", "app", "platform", "login", "register", "static", "kitchen", "staff"}


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60].strip("-")
    return slug if len(slug) >= 2 and slug not in RESERVED_SLUGS else "restaurant"


def _unique_slug(db: Session, base: str) -> str:
    slug, n = base, 2
    while slug in RESERVED_SLUGS or db.scalar(select(Restaurant.id).where(Restaurant.slug == slug)):
        slug = f"{base}-{n}"
        n += 1
    return slug


def _token_response(user: User) -> TokenOut:
    return TokenOut(
        access_token=create_access_token(user.id, user.role, user.restaurant_id),
        user=UserOut.model_validate(user),
        restaurant=user.restaurant,
    )


@router.post("/register-restaurant", response_model=TokenOut, status_code=201,
             dependencies=[Depends(limit("register", 10, 3600))])
def register_restaurant(body: RegisterRestaurantIn, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists")

    if body.slug:
        if body.slug in RESERVED_SLUGS or db.scalar(
            select(Restaurant.id).where(Restaurant.slug == body.slug)
        ):
            raise HTTPException(409, "This restaurant URL is already taken")
        slug = body.slug
    else:
        slug = _unique_slug(db, _slugify(body.restaurant_name))

    restaurant = Restaurant(name=body.restaurant_name.strip(), slug=slug)
    user = User(
        restaurant=restaurant,
        email=email,
        password_hash=hash_password(body.password),
        full_name=body.owner_name.strip(),
        role=OWNER,
    )
    db.add_all([restaurant, user])
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Email or restaurant URL already in use")
    return _token_response(user)


@router.post("/login", response_model=TokenOut, dependencies=[Depends(limit("login", 10, 60))])
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    # Same message for unknown email and wrong password.
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    if not user.is_active:
        raise HTTPException(403, "This account is disabled")
    if user.restaurant is not None and user.restaurant.status != ACTIVE:
        raise HTTPException(403, "This restaurant account is suspended. Please contact support.")
    return _token_response(user)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
