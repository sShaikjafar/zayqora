from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

# Roles (spec section 3.1)
PLATFORM_ADMIN = "platform_admin"
OWNER = "owner"
MANAGER = "manager"
CASHIER = "cashier"
CHEF = "chef"
WAITER = "waiter"
RIDER = "rider"
ACCOUNTANT = "accountant"

ACTIVE = "active"
SUSPENDED = "suspended"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Restaurant(Base):
    """A tenant. Every tenant-owned table must carry restaurant_id."""

    __tablename__ = "restaurants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    plan: Mapped[str] = mapped_column(String(30), default="starter")
    status: Mapped[str] = mapped_column(String(20), default=ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    users: Mapped[list["User"]] = relationship(back_populates="restaurant")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # NULL for platform (VIRELEO) staff
    restaurant_id: Mapped[int | None] = mapped_column(
        ForeignKey("restaurants.id"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(30), default=OWNER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    restaurant: Mapped[Restaurant | None] = relationship(back_populates="users")


class MenuCategory(Base):
    __tablename__ = "menu_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sort_order: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MenuItem(Base):
    __tablename__ = "menu_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("menu_categories.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Money is stored as integer minor units (cents/paise) to avoid float errors.
    price_cents: Mapped[int] = mapped_column(default=0)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    options: Mapped[list["ItemOption"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="ItemOption.id"
    )


class ItemOption(Base):
    """A variant (e.g. size) or an add-on (e.g. extra cheese) of a menu item."""

    __tablename__ = "item_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # "variant" or "addon"
    name: Mapped[str] = mapped_column(String(80))
    price_delta_cents: Mapped[int] = mapped_column(default=0)

    item: Mapped["MenuItem"] = relationship(back_populates="options")


# ------------------------------------------------------------ Orders (Day 3)
from sqlalchemy import JSON, UniqueConstraint  # noqa: E402

ORDER_PENDING = "PENDING"
PAYMENT_PENDING = "PENDING"


class RestaurantSettings(Base):
    """Ordering rules per restaurant (kept in its own table so existing databases keep working)."""

    __tablename__ = "restaurant_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), unique=True, index=True)
    tax_bps: Mapped[int] = mapped_column(default=0)  # 500 = 5%
    delivery_fee_cents: Mapped[int] = mapped_column(default=0)
    min_order_cents: Mapped[int] = mapped_column(default=0)
    pickup_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    delivery_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Max guests seated at the same time for table bookings. 0 = no limit.
    reservation_capacity: Mapped[int] = mapped_column(default=0, server_default="0")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("restaurant_id", "idempotency_key", name="uq_order_idempotency"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default=ORDER_PENDING, index=True)
    fulfilment: Mapped[str] = mapped_column(String(10))  # pickup | delivery
    payment_method: Mapped[str] = mapped_column(String(20), default="cash")
    payment_status: Mapped[str] = mapped_column(String(20), default=PAYMENT_PENDING)
    customer_name: Mapped[str] = mapped_column(String(100))
    customer_phone: Mapped[str] = mapped_column(String(20))
    delivery_address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    subtotal_cents: Mapped[int] = mapped_column(default=0)
    tax_cents: Mapped[int] = mapped_column(default=0)
    delivery_fee_cents: Mapped[int] = mapped_column(default=0)
    total_cents: Mapped[int] = mapped_column(default=0)
    currency: Mapped[str] = mapped_column(String(3))
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    items: Mapped[list["OrderItem"]] = relationship(
        cascade="all, delete-orphan", order_by="OrderItem.id"
    )
    history: Mapped[list["OrderStatusHistory"]] = relationship(
        cascade="all, delete-orphan", order_by="OrderStatusHistory.id"
    )


class OrderItem(Base):
    """Snapshot of what was bought. Never read current menu prices for an old order (spec 13.3)."""

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    menu_item_id: Mapped[int | None] = mapped_column(nullable=True)  # reference only, no FK
    name: Mapped[str] = mapped_column(String(120))
    variant_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    addons: Mapped[list] = mapped_column(JSON, default=list)  # [{"name":..., "price_delta_cents":...}]
    base_price_cents: Mapped[int] = mapped_column(default=0)
    unit_price_cents: Mapped[int] = mapped_column(default=0)
    quantity: Mapped[int] = mapped_column(default=1)
    line_total_cents: Mapped[int] = mapped_column(default=0)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    status: Mapped[str] = mapped_column(String(20))
    changed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ------------------------------------------- Reservations and notifications (Day 4)
class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (UniqueConstraint("restaurant_id", "idempotency_key", name="uq_reservation_idempotency"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="REQUESTED", index=True)
    customer_name: Mapped[str] = mapped_column(String(100))
    customer_phone: Mapped[str] = mapped_column(String(20))
    customer_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    party_size: Mapped[int] = mapped_column(default=2)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # stored in UTC
    duration_minutes: Mapped[int] = mapped_column(default=90)
    notes: Mapped[str | None] = mapped_column(String(300), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    history: Mapped[list["ReservationStatusHistory"]] = relationship(
        cascade="all, delete-orphan", order_by="ReservationStatusHistory.id"
    )


class ReservationStatusHistory(Base):
    __tablename__ = "reservation_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    reservation_id: Mapped[int] = mapped_column(ForeignKey("reservations.id"), index=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    status: Mapped[str] = mapped_column(String(20))
    changed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    """Email outbox. Every email is recorded here; it is sent too when SMTP is configured."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    recipient: Mapped[str] = mapped_column(String(254))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="PENDING")  # PENDING | SENT | FAILED | LOGGED
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ------------------------------------------------------ Platform admin (Day 5)
class AuditLog(Base):
    """Who did what. Written for every platform-admin action and for settings changes."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    restaurant_id: Mapped[int | None] = mapped_column(ForeignKey("restaurants.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


PLANS = ("starter", "growth", "pro")  # placeholder plan names until billing is designed
