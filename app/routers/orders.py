import secrets
from types import SimpleNamespace

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import get_current_restaurant, get_current_user, require_roles
from ..models import (
    ACTIVE,
    AuditLog,
    MANAGER,
    ORDER_PENDING,
    OWNER,
    MenuCategory,
    MenuItem,
    Order,
    OrderItem,
    OrderStatusHistory,
    Restaurant,
    RestaurantSettings,
)
from ..mailer import deliver, queue_staff_notifications
from ..ratelimit import limit
from ..models import User
from ..pricing import compute_totals
from ..workflow import ORDER_REASON_REQUIRED, ORDER_ROLES, order_allowed_next, order_next_statuses
from ..schemas import (
    OrderIn,
    OrderOut,
    OwnerOrderOut,
    QuoteIn,
    QuoteLineOut,
    QuoteOut,
    SettingsOut,
    SettingsUpdate,
    StatusChangeIn,
    TrackOut,
)

router = APIRouter(prefix="/api/v1", tags=["orders"])
can_edit = Depends(require_roles(OWNER, MANAGER))

_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no look-alike characters


def _new_reference() -> str:
    return "ZQ-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(8))


def load_settings(db: Session, restaurant_id: int):
    row = db.scalar(select(RestaurantSettings).where(RestaurantSettings.restaurant_id == restaurant_id))
    if row is not None:
        return row
    return SimpleNamespace(
        tax_bps=0, delivery_fee_cents=0, min_order_cents=0, pickup_enabled=True, delivery_enabled=True,
        reservation_capacity=0,
    )


def _public_restaurant(db: Session, slug: str) -> Restaurant:
    restaurant = db.scalar(select(Restaurant).where(Restaurant.slug == slug))
    if restaurant is None or restaurant.status != ACTIVE:
        raise HTTPException(404, "Restaurant not found")
    return restaurant


# ------------------------------------------------------------------ settings
@router.get("/restaurant/settings", response_model=SettingsOut)
def get_settings(
    restaurant: Restaurant = Depends(get_current_restaurant), db: Session = Depends(get_db)
):
    return load_settings(db, restaurant.id)


@router.patch("/restaurant/settings", response_model=SettingsOut, dependencies=[can_edit])
def update_settings(
    body: SettingsUpdate,
    user: User = Depends(get_current_user),
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(RestaurantSettings).where(RestaurantSettings.restaurant_id == restaurant.id))
    if row is None:
        row = RestaurantSettings(
            restaurant_id=restaurant.id, tax_bps=0, delivery_fee_cents=0, min_order_cents=0,
            pickup_enabled=True, delivery_enabled=True, reservation_capacity=0,
        )
        db.add(row)
    changes = []
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            changes.append(f"{field}={value}")
            setattr(row, field, value)
    if changes:  # critical business settings are audited (spec 17.2)
        db.add(AuditLog(actor_user_id=user.id, restaurant_id=restaurant.id,
                        action="settings.update", detail=", ".join(changes)[:255]))
    db.commit()
    return row


# ------------------------------------------------------------ pricing engine
def _price_cart(db: Session, restaurant: Restaurant, body: QuoteIn):
    """Revalidate every line against the CURRENT menu and compute all money on the server."""
    settings = load_settings(db, restaurant.id)
    if body.fulfilment == "delivery" and not settings.delivery_enabled:
        raise HTTPException(422, "Delivery is not available right now")
    if body.fulfilment == "pickup" and not settings.pickup_enabled:
        raise HTTPException(422, "Pickup is not available right now")

    ids = {line.item_id for line in body.items}
    items = db.scalars(
        select(MenuItem)
        .options(selectinload(MenuItem.options))
        .where(MenuItem.restaurant_id == restaurant.id, MenuItem.id.in_(ids))
    ).all()
    by_id = {i.id: i for i in items}
    active_categories = set(
        db.scalars(
            select(MenuCategory.id).where(
                MenuCategory.restaurant_id == restaurant.id, MenuCategory.is_active.is_(True)
            )
        )
    )

    lines = []
    subtotal = 0
    for line in body.items:
        item = by_id.get(line.item_id)
        if item is None or item.category_id not in active_categories:
            raise HTTPException(409, "An item in your cart is no longer on the menu. Please remove it.")
        if not item.is_available:
            raise HTTPException(409, f"{item.name} is sold out. Please remove it from your cart.")

        variants = {o.id: o for o in item.options if o.kind == "variant"}
        addons = {o.id: o for o in item.options if o.kind == "addon"}

        variant = None
        if variants:
            if line.variant_id is None:
                raise HTTPException(422, f"Please choose an option for {item.name}")
            variant = variants.get(line.variant_id)
            if variant is None:
                raise HTTPException(422, f"Invalid option selected for {item.name}")
        elif line.variant_id is not None:
            raise HTTPException(422, f"{item.name} has no options to choose from")

        if len(set(line.addon_ids)) != len(line.addon_ids):
            raise HTTPException(422, f"Duplicate add-ons selected for {item.name}")
        chosen = []
        for addon_id in line.addon_ids:
            addon = addons.get(addon_id)
            if addon is None:
                raise HTTPException(422, f"Invalid add-on selected for {item.name}")
            chosen.append(addon)

        unit = item.price_cents + (variant.price_delta_cents if variant else 0) + sum(
            a.price_delta_cents for a in chosen
        )
        line_total = unit * line.quantity
        subtotal += line_total
        lines.append({
            "item_id": item.id,
            "name": item.name,
            "variant_name": variant.name if variant else None,
            "addon_names": [a.name for a in chosen],
            "quantity": line.quantity,
            "unit_price_cents": unit,
            "line_total_cents": line_total,
            "note": line.note,
            "_base": item.price_cents,
            "_addons": [{"name": a.name, "price_delta_cents": a.price_delta_cents} for a in chosen],
        })

    if settings.min_order_cents and subtotal < settings.min_order_cents:
        raise HTTPException(
            422, f"The minimum order is {settings.min_order_cents / 100:.2f} {restaurant.currency}"
        )
    totals = compute_totals(subtotal, settings.tax_bps, settings.delivery_fee_cents, body.fulfilment)
    return lines, totals


# ------------------------------------------------------------ public: quote
@router.post("/public/restaurants/{slug}/quote", response_model=QuoteOut,
             dependencies=[Depends(limit("quote", 60, 60))])
def quote(slug: str, body: QuoteIn, db: Session = Depends(get_db)):
    restaurant = _public_restaurant(db, slug)
    lines, totals = _price_cart(db, restaurant, body)
    return QuoteOut(
        lines=[QuoteLineOut(**{k: v for k, v in l.items() if not k.startswith("_")}) for l in lines],
        currency=restaurant.currency,
        **totals,
    )


# ------------------------------------------------------------ public: order
def _existing_by_key(db: Session, restaurant_id: int, key: str):
    return db.scalar(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.restaurant_id == restaurant_id, Order.idempotency_key == key)
    )


@router.post("/public/restaurants/{slug}/orders", response_model=OrderOut, status_code=201,
             dependencies=[Depends(limit("order", 10, 60))])
def create_order(
    slug: str,
    body: OrderIn,
    response: Response,
    background: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=80),
    db: Session = Depends(get_db),
):
    restaurant = _public_restaurant(db, slug)

    # Idempotency: a repeated submit returns the original order instead of creating a duplicate.
    if idempotency_key:
        existing = _existing_by_key(db, restaurant.id, idempotency_key)
        if existing is not None:
            response.status_code = 200
            return existing

    if body.fulfilment == "delivery" and not (body.delivery_address and len(body.delivery_address) >= 5):
        raise HTTPException(422, "A delivery address is required for delivery orders")

    lines, totals = _price_cart(db, restaurant, body)

    # Spec 16.4: if the price changed since the customer saw the quote, they must confirm again.
    if body.expected_total_cents is not None and body.expected_total_cents != totals["total_cents"]:
        raise HTTPException(409, {
            "code": "PRICE_CHANGED",
            "message": "Prices or fees changed. Please review the updated total and confirm.",
            "total_cents": totals["total_cents"],
        })

    order = Order(
        restaurant_id=restaurant.id,
        reference=_new_reference(),
        status=ORDER_PENDING,
        fulfilment=body.fulfilment,
        payment_method="cash",
        customer_name=body.customer_name,
        customer_phone=body.customer_phone,
        delivery_address=body.delivery_address if body.fulfilment == "delivery" else None,
        notes=body.notes,
        currency=restaurant.currency,
        idempotency_key=idempotency_key,
        **totals,
    )
    for l in lines:
        order.items.append(OrderItem(
            restaurant_id=restaurant.id,
            menu_item_id=l["item_id"],
            name=l["name"],
            variant_name=l["variant_name"],
            addons=l["_addons"],
            base_price_cents=l["_base"],
            unit_price_cents=l["unit_price_cents"],
            quantity=l["quantity"],
            line_total_cents=l["line_total_cents"],
            note=l["note"],
        ))
    order.history.append(OrderStatusHistory(restaurant_id=restaurant.id, status=ORDER_PENDING, note="Order placed"))
    db.add(order)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if idempotency_key:  # a concurrent identical request won the race
            existing = _existing_by_key(db, restaurant.id, idempotency_key)
            if existing is not None:
                response.status_code = 200
                return existing
        raise HTTPException(409, "Could not place the order. Please try again.")

    # Tell the restaurant about the new order. Email problems must never fail the order itself.
    try:
        items_text = "\n".join(
            f"  {i.quantity} x {i.name}" + (f" ({i.variant_name})" if i.variant_name else "")
            + (" + " + ", ".join(a["name"] for a in i.addons) if i.addons else "")
            for i in order.items
        )
        mail_body = (
            f"New {order.fulfilment} order {order.reference}\n\n{items_text}\n\n"
            f"Total: {order.total_cents / 100:.2f} {order.currency} (cash)\n"
            f"Customer: {order.customer_name}, {order.customer_phone}\n"
            + (f"Address: {order.delivery_address}\n" if order.delivery_address else "")
            + (f"Notes: {order.notes}\n" if order.notes else "")
        )
        ids = queue_staff_notifications(
            db, restaurant.id, "order_created",
            f"New order {order.reference} - {order.total_cents / 100:.2f} {order.currency}", mail_body,
        )
        background.add_task(deliver, ids)
    except Exception:  # noqa: BLE001
        db.rollback()
    return order


# ------------------------------------------------ public: customer order tracking
@router.get("/public/restaurants/{slug}/orders/{reference}", response_model=TrackOut,
            dependencies=[Depends(limit("track", 60, 60))])
def track_order(slug: str, reference: str, db: Session = Depends(get_db)):
    """The reference is an unguessable code, so it acts as the customer's access key to this one order."""
    restaurant = _public_restaurant(db, slug)
    order = db.scalar(
        select(Order).options(selectinload(Order.items), selectinload(Order.history))
        .where(Order.restaurant_id == restaurant.id, Order.reference == reference.strip().upper()))
    if order is None:
        raise HTTPException(404, "We could not find an order with that reference")
    out = TrackOut.model_validate(order)
    for h in out.history:  # internal notes stay private; only a rejection/cancellation reason is shared
        if h.status not in ("REJECTED", "CANCELLED"):
            h.note = None
    return out


# ------------------------------------------------- owner: orders and status flow
def _load_order(db: Session, restaurant: Restaurant, order_id: int) -> Order:
    order = db.scalar(
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.history))
        .where(Order.id == order_id, Order.restaurant_id == restaurant.id)
    )
    if order is None:
        raise HTTPException(404, "Order not found")
    return order


def _owner_view(order: Order, user: User) -> OwnerOrderOut:
    out = OwnerOrderOut.model_validate(order)
    out.allowed_next = order_allowed_next(order.status, order.fulfilment, user.role)
    return out


@router.get("/orders", response_model=list[OwnerOrderOut])
def list_orders(
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: Session = Depends(get_db),
):
    orders = db.scalars(
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.history))
        .where(Order.restaurant_id == restaurant.id)
        .order_by(Order.id.desc())
        .limit(limit)
    ).all()
    return [_owner_view(o, user) for o in orders]


@router.get("/orders/{order_id}", response_model=OwnerOrderOut)
def get_order(
    order_id: int,
    user: User = Depends(get_current_user),
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: Session = Depends(get_db),
):
    return _owner_view(_load_order(db, restaurant, order_id), user)


@router.post("/orders/{order_id}/status", response_model=OwnerOrderOut)
def change_order_status(
    order_id: int,
    body: StatusChangeIn,
    user: User = Depends(get_current_user),
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: Session = Depends(get_db),
):
    order = _load_order(db, restaurant, order_id)
    target = body.status

    # 0. No one can move an order into a status that is not a real step (e.g. back to PENDING)
    if target not in ORDER_ROLES:
        raise HTTPException(409, f"An order cannot be changed to {target}")
    # 1. Is this role allowed to perform this step? (enforced on the backend)
    if user.role not in ORDER_ROLES.get(target, set()):
        raise HTTPException(403, "You do not have permission to make this change")
    # 2. Is this a legal step from the current status?
    if target not in order_next_statuses(order.status, order.fulfilment):
        raise HTTPException(409, f"An order that is {order.status} cannot be changed to {target}")
    # 3. Rejections and cancellations need a reason
    note = (body.note or "").strip() or None
    if target in ORDER_REASON_REQUIRED and not note:
        raise HTTPException(422, "Please give a reason")

    order.status = target
    order.history.append(
        OrderStatusHistory(restaurant_id=restaurant.id, status=target, changed_by_user_id=user.id, note=note)
    )
    if target == "COMPLETED" and order.payment_method == "cash":
        order.payment_status = "PAID"  # cash collected on pickup / delivery
    elif target in ("REJECTED", "CANCELLED") and order.payment_status == "PENDING":
        order.payment_status = "CANCELLED"
    db.commit()
    return _owner_view(order, user)
