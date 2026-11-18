from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_restaurant, require_roles
from ..models import ACTIVE, MANAGER, OWNER, Restaurant
from ..schemas import PublicRestaurantOut, RestaurantOut, RestaurantUpdate

router = APIRouter(prefix="/api/v1", tags=["restaurant"])


@router.get("/restaurant", response_model=RestaurantOut)
def get_my_restaurant(restaurant: Restaurant = Depends(get_current_restaurant)):
    return restaurant


@router.patch(
    "/restaurant",
    response_model=RestaurantOut,
    dependencies=[Depends(require_roles(OWNER, MANAGER))],
)
def update_my_restaurant(
    body: RestaurantUpdate,
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: Session = Depends(get_db),
):
    data = body.model_dump(exclude_unset=True)
    if "currency" in data and data["currency"]:
        data["currency"] = data["currency"].upper()
    for field, value in data.items():
        setattr(restaurant, field, value)
    db.add(restaurant)
    db.commit()
    return restaurant


@router.get("/public/restaurants/{slug}", response_model=PublicRestaurantOut)
def public_restaurant(slug: str, db: Session = Depends(get_db)):
    restaurant = db.scalar(select(Restaurant).where(Restaurant.slug == slug))
    if restaurant is None or restaurant.status != ACTIVE:
        raise HTTPException(404, "Restaurant not found")
    return restaurant
