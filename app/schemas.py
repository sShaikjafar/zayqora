from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class RegisterRestaurantIn(BaseModel):
    restaurant_name: str = Field(min_length=2, max_length=120)
    owner_name: str = Field(min_length=2, max_length=120)
    email: str = Field(pattern=_EMAIL, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    slug: str | None = Field(
        default=None, min_length=2, max_length=60, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    full_name: str
    role: str
    restaurant_id: int | None


class RestaurantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    description: str | None
    phone: str | None
    address: str | None
    currency: str
    timezone: str
    plan: str
    status: str
    created_at: datetime


class PublicRestaurantOut(BaseModel):
    """What anonymous visitors may see. No plan, status or internal ids."""

    model_config = ConfigDict(from_attributes=True)
    name: str
    slug: str
    description: str | None
    phone: str | None
    address: str | None
    currency: str
    timezone: str


class RestaurantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=255)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    timezone: str | None = Field(default=None, max_length=64)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
    restaurant: RestaurantOut | None


# ---------------------------------------------------------------- Menu (Day 2)
from typing import Literal  # noqa: E402

_MAX_MONEY = 10_000_000  # 100,000.00 in minor units


class _Clean(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class OptionIn(_Clean):
    kind: Literal["variant", "addon"]
    name: str = Field(min_length=1, max_length=80)
    price_delta_cents: int = Field(default=0, ge=0, le=_MAX_MONEY)


class OptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    name: str
    price_delta_cents: int


class CategoryIn(_Clean):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    sort_order: int = Field(default=0, ge=0, le=100000)
    is_active: bool = True


class CategoryUpdate(_Clean):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    sort_order: int | None = Field(default=None, ge=0, le=100000)
    is_active: bool | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str | None
    sort_order: int
    is_active: bool


class ItemIn(_Clean):
    category_id: int
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    price_cents: int = Field(ge=0, le=_MAX_MONEY)
    is_available: bool = True
    sort_order: int = Field(default=0, ge=0, le=100000)
    options: list[OptionIn] = Field(default_factory=list, max_length=30)


class ItemUpdate(_Clean):
    category_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    price_cents: int | None = Field(default=None, ge=0, le=_MAX_MONEY)
    is_available: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100000)
    options: list[OptionIn] | None = Field(default=None, max_length=30)


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    category_id: int
    name: str
    description: str | None
    price_cents: int
    is_available: bool
    sort_order: int
    options: list[OptionOut]


class PublicOptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    name: str
    price_delta_cents: int


class PublicItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str | None
    price_cents: int
    is_available: bool
    options: list[PublicOptionOut]


class PublicCategoryOut(BaseModel):
    name: str
    description: str | None
    items: list[PublicItemOut]


class PublicMenuOut(BaseModel):
    restaurant: PublicRestaurantOut
    categories: list[PublicCategoryOut]


# ---------------------------------------------------------- Orders (Day 3)
class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    tax_bps: int
    delivery_fee_cents: int
    min_order_cents: int
    pickup_enabled: bool
    delivery_enabled: bool
    reservation_capacity: int


class SettingsUpdate(BaseModel):
    tax_bps: int | None = Field(default=None, ge=0, le=5000)
    delivery_fee_cents: int | None = Field(default=None, ge=0, le=_MAX_MONEY)
    min_order_cents: int | None = Field(default=None, ge=0, le=_MAX_MONEY)
    pickup_enabled: bool | None = None
    delivery_enabled: bool | None = None
    reservation_capacity: int | None = Field(default=None, ge=0, le=1000)


Fulfilment = Literal["pickup", "delivery"]


class CartLineIn(_Clean):
    item_id: int
    quantity: int = Field(ge=1, le=50)
    variant_id: int | None = None
    addon_ids: list[int] = Field(default_factory=list, max_length=20)
    note: str | None = Field(default=None, max_length=200)
    # NOTE: there is deliberately no price field. Prices are always computed on the server.


class QuoteIn(_Clean):
    fulfilment: Fulfilment
    items: list[CartLineIn] = Field(min_length=1, max_length=50)


class OrderIn(QuoteIn):
    customer_name: str = Field(min_length=2, max_length=100)
    customer_phone: str = Field(pattern=r"^[0-9+()\-\s]{7,20}$")
    delivery_address: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=500)
    expected_total_cents: int | None = Field(default=None, ge=0)


class QuoteLineOut(BaseModel):
    item_id: int
    name: str
    variant_name: str | None
    addon_names: list[str]
    quantity: int
    unit_price_cents: int
    line_total_cents: int
    note: str | None


class QuoteOut(BaseModel):
    lines: list[QuoteLineOut]
    subtotal_cents: int
    tax_cents: int
    delivery_fee_cents: int
    total_cents: int
    currency: str


class AddonSnapshotOut(BaseModel):
    name: str
    price_delta_cents: int


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str
    variant_name: str | None
    addons: list[AddonSnapshotOut]
    quantity: int
    unit_price_cents: int
    line_total_cents: int
    note: str | None


class OrderOut(BaseModel):
    """Public view (shown to the customer). No internal numeric ids."""

    model_config = ConfigDict(from_attributes=True)
    reference: str
    status: str
    fulfilment: str
    payment_method: str
    payment_status: str
    customer_name: str
    customer_phone: str
    delivery_address: str | None
    notes: str | None
    items: list[OrderItemOut]
    subtotal_cents: int
    tax_cents: int
    delivery_fee_cents: int
    total_cents: int
    currency: str
    created_at: datetime


OrderStatus = Literal[
    "PENDING", "CONFIRMED", "PREPARING", "READY", "OUT_FOR_DELIVERY", "COMPLETED", "REJECTED", "CANCELLED"
]


class StatusChangeIn(_Clean):
    status: OrderStatus
    note: str | None = Field(default=None, max_length=255)


class StatusHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: str
    note: str | None
    created_at: datetime
    changed_by_user_id: int | None


class OwnerOrderOut(OrderOut):
    id: int
    history: list[StatusHistoryOut] = Field(default_factory=list)
    allowed_next: list[str] = Field(default_factory=list)  # what THIS user may do next


# ------------------------------------------- Reservations and notifications (Day 4)
from datetime import timezone as _tz  # noqa: E402
from pydantic import field_validator  # noqa: E402


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=_tz.utc)


ReservationStatus = Literal["REQUESTED", "CONFIRMED", "SEATED", "COMPLETED", "DECLINED", "CANCELLED", "NO_SHOW"]


class ReservationIn(_Clean):
    customer_name: str = Field(min_length=2, max_length=100)
    customer_phone: str = Field(pattern=r"^[0-9+()\-\s]{7,20}$")
    customer_email: str | None = Field(default=None, max_length=254, pattern=_EMAIL)
    party_size: int = Field(ge=1, le=20)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")  # the restaurant's local date
    time: str = Field(pattern=r"^\d{2}:\d{2}$")  # the restaurant's local time
    notes: str | None = Field(default=None, max_length=300)


class ReservationStatusChangeIn(_Clean):
    status: ReservationStatus
    note: str | None = Field(default=None, max_length=255)


class ReservationHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: str
    note: str | None
    created_at: datetime
    changed_by_user_id: int | None

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v):
        return _as_utc(v)


class PublicReservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    reference: str
    status: str
    customer_name: str
    party_size: int
    starts_at: datetime
    duration_minutes: int
    notes: str | None

    @field_validator("starts_at")
    @classmethod
    def _utc(cls, v):
        return _as_utc(v)


class OwnerReservationOut(PublicReservationOut):
    id: int
    customer_phone: str
    customer_email: str | None
    history: list[ReservationHistoryOut] = Field(default_factory=list)
    allowed_next: list[str] = Field(default_factory=list)


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    recipient: str
    subject: str
    status: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v):
        return _as_utc(v)


# ------------------------------------------------------------- Day 5
class ReportDay(BaseModel):
    date: str
    orders: int
    revenue_cents: int


class ReportItem(BaseModel):
    name: str
    quantity: int
    revenue_cents: int


class ReportRevenue(BaseModel):
    completed_orders: int
    subtotal_cents: int
    tax_cents: int
    delivery_fee_cents: int
    total_cents: int
    average_order_cents: int


class ReportOut(BaseModel):
    from_date: str
    to_date: str
    timezone: str
    currency: str
    orders_total: int
    orders_by_status: dict[str, int]
    revenue: ReportRevenue
    daily: list[ReportDay]
    top_items: list[ReportItem]
    reservations_total: int
    reservations_by_status: dict[str, int]


class TrackHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: str
    note: str | None
    created_at: datetime


class TrackOut(OrderOut):
    """What a customer sees when tracking their own order by its reference."""

    history: list[TrackHistoryOut] = Field(default_factory=list)


class AdminRestaurantOut(BaseModel):
    id: int
    name: str
    slug: str
    plan: str
    status: str
    created_at: datetime
    owner_email: str | None
    order_count: int


class AdminStatusIn(_Clean):
    status: Literal["active", "suspended"]
    reason: str | None = Field(default=None, max_length=255)


class AdminPlanIn(_Clean):
    plan: Literal["starter", "growth", "pro"]


class AuditOut(BaseModel):
    id: int
    action: str
    detail: str | None
    restaurant_id: int | None
    actor_email: str | None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v):
        return _as_utc(v)
