from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import get_current_restaurant, require_roles
from ..models import ACTIVE, MANAGER, OWNER, ItemOption, MenuCategory, MenuItem, Restaurant
from ..schemas import (
    CategoryIn,
    CategoryOut,
    CategoryUpdate,
    ItemIn,
    ItemOut,
    ItemUpdate,
    PublicCategoryOut,
    PublicItemOut,
    PublicMenuOut,
    PublicRestaurantOut,
)

router = APIRouter(prefix="/api/v1", tags=["menu"])

# Reading the menu: any staff member of the restaurant.
# Changing the menu: owner or manager only (enforced on the backend).
can_edit = Depends(require_roles(OWNER, MANAGER))


# ------------------------------------------------------------------ helpers
# Every query below filters by restaurant_id. That filter IS the tenant isolation.
def _category_or_404(db: Session, restaurant: Restaurant, category_id: int) -> MenuCategory:
    cat = db.scalar(
        select(MenuCategory).where(
            MenuCategory.id == category_id, MenuCategory.restaurant_id == restaurant.id
        )
    )
    if cat is None:
        raise HTTPException(404, "Category not found")
    return cat


def _item_or_404(db: Session, restaurant: Restaurant, item_id: int) -> MenuItem:
    item = db.scalar(
        select(MenuItem)
        .options(selectinload(MenuItem.options))
        .where(MenuItem.id == item_id, MenuItem.restaurant_id == restaurant.id)
    )
    if item is None:
        raise HTTPException(404, "Menu item not found")
    return item


# --------------------------------------------------------------- categories
@router.get("/menu/categories", response_model=list[CategoryOut])
def list_categories(
    restaurant: Restaurant = Depends(get_current_restaurant), db: Session = Depends(get_db)
):
    return db.scalars(
        select(MenuCategory)
        .where(MenuCategory.restaurant_id == restaurant.id)
        .order_by(MenuCategory.sort_order, MenuCategory.id)
    ).all()


@router.post("/menu/categories", response_model=CategoryOut, status_code=201, dependencies=[can_edit])
def create_category(
    body: CategoryIn,
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: Session = Depends(get_db),
):
    cat = MenuCategory(restaurant_id=restaurant.id, **body.model_dump())
    db.add(cat)
    db.commit()
    return cat


@router.patch("/menu/categories/{category_id}", response_model=CategoryOut, dependencies=[can_edit])
def update_category(
    category_id: int,
    body: CategoryUpdate,
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: Session = Depends(get_db),
):
    cat = _category_or_404(db, restaurant, category_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is None and field != "description":
            continue
        setattr(cat, field, value)
    db.commit()
    return cat


@router.delete("/menu/categories/{category_id}", status_code=204, dependencies=[can_edit])
def delete_category(
    category_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: Session = Depends(get_db),
):
    cat = _category_or_404(db, restaurant, category_id)
    count = db.scalar(
        select(func.count()).select_from(MenuItem).where(
            MenuItem.category_id == cat.id, MenuItem.restaurant_id == restaurant.id
        )
    )
    if count:
        raise HTTPException(409, "This category still has items. Move or delete them first.")
    db.delete(cat)
    db.commit()
    return Response(status_code=204)


# -------------------------------------------------------------------- items
@router.get("/menu/items", response_model=list[ItemOut])
def list_items(
    category_id: int | None = None,
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: Session = Depends(get_db),
):
    query = (
        select(MenuItem)
        .options(selectinload(MenuItem.options))
        .where(MenuItem.restaurant_id == restaurant.id)
        .order_by(MenuItem.sort_order, MenuItem.id)
    )
    if category_id is not None:
        query = query.where(MenuItem.category_id == category_id)
    return db.scalars(query).all()


@router.get("/menu/items/{item_id}", response_model=ItemOut)
def get_item(
    item_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: Session = Depends(get_db),
):
    return _item_or_404(db, restaurant, item_id)


@router.post("/menu/items", response_model=ItemOut, status_code=201, dependencies=[can_edit])
def create_item(
    body: ItemIn,
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: Session = Depends(get_db),
):
    _category_or_404(db, restaurant, body.category_id)  # must be this restaurant's category
    data = body.model_dump(exclude={"options"})
    item = MenuItem(restaurant_id=restaurant.id, **data)
    for opt in body.options:
        item.options.append(ItemOption(restaurant_id=restaurant.id, **opt.model_dump()))
    db.add(item)
    db.commit()
    return item


@router.patch("/menu/items/{item_id}", response_model=ItemOut, dependencies=[can_edit])
def update_item(
    item_id: int,
    body: ItemUpdate,
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: Session = Depends(get_db),
):
    item = _item_or_404(db, restaurant, item_id)
    data = body.model_dump(exclude_unset=True)
    options = data.pop("options", None)

    if data.get("category_id") is not None:
        _category_or_404(db, restaurant, data["category_id"])

    for field, value in data.items():
        if value is None and field != "description":
            continue
        setattr(item, field, value)

    if options is not None:  # replace the full option list
        item.options.clear()
        for opt in options:
            item.options.append(ItemOption(restaurant_id=restaurant.id, **opt))

    db.commit()
    return item


@router.delete("/menu/items/{item_id}", status_code=204, dependencies=[can_edit])
def delete_item(
    item_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: Session = Depends(get_db),
):
    item = _item_or_404(db, restaurant, item_id)
    db.delete(item)
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------- public menu
@router.get("/public/restaurants/{slug}/menu", response_model=PublicMenuOut)
def public_menu(slug: str, db: Session = Depends(get_db)):
    restaurant = db.scalar(select(Restaurant).where(Restaurant.slug == slug))
    if restaurant is None or restaurant.status != ACTIVE:
        raise HTTPException(404, "Restaurant not found")

    categories = db.scalars(
        select(MenuCategory)
        .where(MenuCategory.restaurant_id == restaurant.id, MenuCategory.is_active.is_(True))
        .order_by(MenuCategory.sort_order, MenuCategory.id)
    ).all()
    items = db.scalars(
        select(MenuItem)
        .options(selectinload(MenuItem.options))
        .where(MenuItem.restaurant_id == restaurant.id)
        .order_by(MenuItem.sort_order, MenuItem.id)
    ).all()

    by_category: dict[int, list[PublicItemOut]] = {}
    for item in items:
        by_category.setdefault(item.category_id, []).append(PublicItemOut.model_validate(item))

    return PublicMenuOut(
        restaurant=PublicRestaurantOut.model_validate(restaurant),
        categories=[
            PublicCategoryOut(name=c.name, description=c.description, items=by_category[c.id])
            for c in categories
            if c.id in by_category  # hide empty categories from customers
        ],
    )
