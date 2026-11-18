"""Command-line helpers.

  python -m app.manage create-admin you@example.com      create a VIRELEO platform administrator
  python -m app.manage seed-demo                         create a demo restaurant with sample data
"""
import argparse
import getpass
import random
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .database import Base, SessionLocal, engine
from .migrate import ensure_columns
from .models import (
    CASHIER, CHEF, OWNER, PLATFORM_ADMIN, AuditLog, ItemOption, MenuCategory, MenuItem, Order, OrderItem,
    OrderStatusHistory, Reservation, ReservationStatusHistory, Restaurant, RestaurantSettings, User,
)
from .pricing import compute_totals
from .routers.orders import _new_reference as order_reference
from .routers.reservations import _new_reference as reservation_reference
from .security import hash_password

DEMO_PASSWORD = "Demo12345!"
DEMO_USERS = [
    ("demo@zayqora.test", "Demo Owner", OWNER),
    ("chef@zayqora.test", "Demo Chef", CHEF),
    ("cashier@zayqora.test", "Demo Cashier", CASHIER),
]
# (category, [(name, description, price_cents, [(kind, option name, price_delta_cents)])])
DEMO_MENU = [
    ("Starters", [
        ("Paneer Tikka", "Char-grilled paneer with house spices", 22000, [("variant", "Half", 0), ("variant", "Full", 10000)]),
        ("Chicken 65", "Crisp fried chicken tossed with curry leaves", 24000, []),
    ]),
    ("Mains", [
        ("Veg Biryani", "Fragrant basmati rice with vegetables", 20000,
         [("variant", "Half", 0), ("variant", "Full", 8000), ("addon", "Extra raita", 2000)]),
        ("Chicken Biryani", "Slow-cooked with basmati rice and house spices", 26000,
         [("variant", "Half", 0), ("variant", "Full", 10000), ("addon", "Extra raita", 2000)]),
        ("Butter Naan", "Soft tandoor bread", 4000, []),
    ]),
    ("Desserts", [("Gulab Jamun", "Warm milk dumplings in rose syrup", 9000, [])]),
]


def _init() -> None:
    Base.metadata.create_all(engine)
    ensure_columns(engine)


def create_admin(email: str, password: str | None = None, name: str = "Platform Admin") -> int:
    _init()
    email = email.strip().lower()
    if password is None:
        password = getpass.getpass("Password (at least 12 characters): ")
    if len(password) < 12:
        print("The password must be at least 12 characters long.")
        return 1
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.email == email)):
            print("A user with this email already exists.")
            return 1
        admin = User(restaurant_id=None, email=email, password_hash=hash_password(password),
                     full_name=name, role=PLATFORM_ADMIN)
        db.add(admin)
        db.flush()
        db.add(AuditLog(actor_user_id=None, action="admin.create", detail=email))
        db.commit()
    print(f"Platform admin {email} created. Log in at /admin")
    return 0


def seed_demo() -> int:
    _init()
    with SessionLocal() as db:
        if db.scalar(select(Restaurant.id).where(Restaurant.slug == "demo-bistro")):
            print("The demo restaurant already exists. Nothing was changed.")
            return 0
        tz = "UTC"
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo("Asia/Kolkata")
            tz = "Asia/Kolkata"
        except Exception:  # timezone database not installed
            pass
        restaurant = Restaurant(
            name="Demo Bistro", slug="demo-bistro", currency="INR", timezone=tz,
            description="A sample restaurant with South Indian favourites, used for demonstrations.",
            phone="+91 98765 00000", address="Road No. 1, Banjara Hills, Hyderabad")
        db.add(restaurant)
        db.flush()
        rid = restaurant.id
        password_hash = hash_password(DEMO_PASSWORD)
        db.add_all([User(restaurant_id=rid, email=e, password_hash=password_hash, full_name=n, role=r)
                    for e, n, r in DEMO_USERS])
        db.add(RestaurantSettings(restaurant_id=rid, tax_bps=500, delivery_fee_cents=3000, min_order_cents=0,
                                  pickup_enabled=True, delivery_enabled=True, reservation_capacity=20))

        built: list[tuple[MenuItem, list[ItemOption]]] = []
        for c_index, (cat_name, dishes) in enumerate(DEMO_MENU):
            category = MenuCategory(restaurant_id=rid, name=cat_name, sort_order=c_index)
            db.add(category)
            db.flush()
            for d_index, (name, desc, price, options) in enumerate(dishes):
                item = MenuItem(restaurant_id=rid, category_id=category.id, name=name, description=desc,
                                price_cents=price, sort_order=d_index)
                item.options = [ItemOption(restaurant_id=rid, kind=k, name=n, price_delta_cents=p)
                                for k, n, p in options]
                db.add(item)
                built.append((item, item.options))
        db.flush()

        rng = random.Random(7)
        now = datetime.now(timezone.utc)
        for n in range(28):
            fulfilment = rng.choice(["pickup", "delivery"])
            created = now - timedelta(days=rng.randint(0, 13), hours=rng.randint(0, 10), minutes=rng.randint(0, 59))
            lines, subtotal = [], 0
            for item, opts in rng.sample(built, rng.randint(1, 3)):
                variants = [o for o in opts if o.kind == "variant"]
                addons = [o for o in opts if o.kind == "addon"]
                variant = rng.choice(variants) if variants else None
                chosen = [a for a in addons if rng.random() < 0.4]
                unit = item.price_cents + (variant.price_delta_cents if variant else 0) + sum(
                    a.price_delta_cents for a in chosen)
                qty = rng.randint(1, 3)
                subtotal += unit * qty
                lines.append(OrderItem(
                    restaurant_id=rid, menu_item_id=item.id, name=item.name,
                    variant_name=variant.name if variant else None,
                    addons=[{"name": a.name, "price_delta_cents": a.price_delta_cents} for a in chosen],
                    base_price_cents=item.price_cents, unit_price_cents=unit, quantity=qty,
                    line_total_cents=unit * qty))
            totals = compute_totals(subtotal, 500, 3000, fulfilment)

            roll = rng.random()
            if n >= 26:
                status, created = rng.choice(["PENDING", "PREPARING"]), now - timedelta(minutes=rng.randint(5, 40))
            elif roll < 0.08:
                status = "CANCELLED"
            elif roll < 0.15:
                status = "REJECTED"
            else:
                status = "COMPLETED"
            path = {
                "PENDING": ["PENDING"], "PREPARING": ["PENDING", "CONFIRMED", "PREPARING"],
                "REJECTED": ["PENDING", "REJECTED"], "CANCELLED": ["PENDING", "CONFIRMED", "CANCELLED"],
                "COMPLETED": ["PENDING", "CONFIRMED", "PREPARING", "READY"]
                + (["OUT_FOR_DELIVERY"] if fulfilment == "delivery" else []) + ["COMPLETED"],
            }[status]
            notes = {"REJECTED": "Out of stock", "CANCELLED": "Customer asked to cancel"}
            order = Order(
                restaurant_id=rid, reference=order_reference(), status=status, fulfilment=fulfilment,
                payment_method="cash",
                payment_status="PAID" if status == "COMPLETED" else ("CANCELLED" if status in notes else "PENDING"),
                customer_name=rng.choice(["Asha Rao", "Ravi Kumar", "Meena Iyer", "Ganesh Reddy", "Priya Nair"]),
                customer_phone=f"98{rng.randint(10000000, 99999999)}",
                delivery_address="Plot 12, Jubilee Hills, Hyderabad" if fulfilment == "delivery" else None,
                currency="INR", created_at=created, **totals)
            order.items = lines
            order.history = [
                OrderStatusHistory(restaurant_id=rid, status=s, note=notes.get(s), created_at=created + timedelta(minutes=7 * i))
                for i, s in enumerate(path)]
            db.add(order)

        def reservation(days: int, hour: int, status: str, name: str, party: int, path: list[str], email=None):
            start = (now + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
            res = Reservation(restaurant_id=rid, reference=reservation_reference(), status=status,
                              customer_name=name, customer_phone="9876543210", customer_email=email,
                              party_size=party, starts_at=start, notes=None)
            res.history = [ReservationStatusHistory(restaurant_id=rid, status=s, created_at=now - timedelta(days=abs(days) + 1))
                           for s in path]
            db.add(res)

        reservation(-1, 19, "COMPLETED", "Asha Rao", 4, ["REQUESTED", "CONFIRMED", "SEATED", "COMPLETED"])
        reservation(-2, 20, "NO_SHOW", "Ravi Kumar", 2, ["REQUESTED", "CONFIRMED", "NO_SHOW"])
        reservation(1, 19, "CONFIRMED", "Meena Iyer", 6, ["REQUESTED", "CONFIRMED"], "meena@example.com")
        reservation(3, 13, "REQUESTED", "Priya Nair", 3, ["REQUESTED"])
        db.commit()

    print("Demo restaurant created: /r/demo-bistro")
    for email, _, role in DEMO_USERS:
        print(f"  {role:8} {email}   password: {DEMO_PASSWORD}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.manage")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-admin", help="create a platform administrator")
    p.add_argument("email")
    p.add_argument("--password", help="omit to be asked securely")
    p.add_argument("--name", default="Platform Admin")
    sub.add_parser("seed-demo", help="create a demo restaurant with sample data")
    args = parser.parse_args(argv)
    if args.command == "create-admin":
        return create_admin(args.email, args.password, args.name)
    return seed_demo()


if __name__ == "__main__":
    sys.exit(main())
