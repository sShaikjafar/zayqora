import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import get_current_restaurant, get_current_user, require_roles
from ..mailer import deliver, queue_notification, queue_staff_notifications
from ..models import MANAGER, OWNER, Reservation, ReservationStatusHistory, Restaurant, User
from ..schemas import (
    NotificationOut,
    OwnerReservationOut,
    PublicReservationOut,
    ReservationIn,
    ReservationStatusChangeIn,
)
from ..models import Notification
from ..workflow import (
    RESERVATION_REASON_REQUIRED,
    RESERVATION_ROLES,
    reservation_allowed_next,
    reservation_next_statuses,
)
from ..ratelimit import limit
from .orders import _public_restaurant, load_settings

router = APIRouter(prefix="/api/v1", tags=["reservations"])

_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
DEFAULT_DURATION = 90  # minutes a booking holds its seats


def _new_reference() -> str:
    return "RS-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(6))


def _zone(name: str | None):
    """Spec 16.5: store times in UTC, interpret the customer's time in the restaurant's timezone."""
    if not name or name.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "This restaurant's timezone is not set up correctly")


def _local_text(res: Reservation, zone) -> str:
    start = res.starts_at if res.starts_at.tzinfo else res.starts_at.replace(tzinfo=timezone.utc)
    return start.astimezone(zone).strftime("%a %d %b %Y, %H:%M")


# ---------------------------------------------------------- public request
@router.post("/public/restaurants/{slug}/reservations", response_model=PublicReservationOut, status_code=201,
             dependencies=[Depends(limit("reservation", 6, 60))])
def create_reservation(
    slug: str,
    body: ReservationIn,
    response: Response,
    background: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=80),
    db: Session = Depends(get_db),
):
    restaurant = _public_restaurant(db, slug)

    if idempotency_key:
        existing = db.scalar(select(Reservation).where(
            Reservation.restaurant_id == restaurant.id, Reservation.idempotency_key == idempotency_key))
        if existing is not None:
            response.status_code = 200
            return existing

    zone = _zone(restaurant.timezone)
    try:
        local = datetime.strptime(f"{body.date} {body.time}", "%Y-%m-%d %H:%M").replace(tzinfo=zone)
    except ValueError:
        raise HTTPException(422, "Please enter a valid date and time")
    starts_utc = local.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if starts_utc < now + timedelta(minutes=30):
        raise HTTPException(422, "Please choose a time at least 30 minutes from now")
    if starts_utc > now + timedelta(days=90):
        raise HTTPException(422, "Reservations can be made up to 90 days ahead")

    # Capacity rule: guests whose bookings overlap this one must not exceed the restaurant's capacity.
    capacity = getattr(load_settings(db, restaurant.id), "reservation_capacity", 0) or 0
    if capacity:
        if body.party_size > capacity:
            raise HTTPException(422, f"We can seat at most {capacity} guests at a time")
        new_end = starts_utc + timedelta(minutes=DEFAULT_DURATION)
        nearby = db.scalars(select(Reservation).where(
            Reservation.restaurant_id == restaurant.id,
            Reservation.status.in_(("REQUESTED", "CONFIRMED", "SEATED")),
            Reservation.starts_at > starts_utc - timedelta(hours=6),
            Reservation.starts_at < new_end)).all()
        booked = 0
        for other in nearby:
            other_start = other.starts_at if other.starts_at.tzinfo else other.starts_at.replace(tzinfo=timezone.utc)
            if other_start < new_end and other_start + timedelta(minutes=other.duration_minutes) > starts_utc:
                booked += other.party_size
        if booked + body.party_size > capacity:
            raise HTTPException(409, "Sorry, we are fully booked around that time. Please choose another time.")

    res = Reservation(
        restaurant_id=restaurant.id, reference=_new_reference(), status="REQUESTED",
        customer_name=body.customer_name, customer_phone=body.customer_phone,
        customer_email=body.customer_email, party_size=body.party_size, starts_at=starts_utc,
        notes=body.notes, idempotency_key=idempotency_key,
    )
    res.history.append(ReservationStatusHistory(
        restaurant_id=restaurant.id, status="REQUESTED", note="Reservation requested"))
    db.add(res)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            existing = db.scalar(select(Reservation).where(
                Reservation.restaurant_id == restaurant.id, Reservation.idempotency_key == idempotency_key))
            if existing is not None:
                response.status_code = 200
                return existing
        raise HTTPException(409, "Could not save the reservation. Please try again.")

    try:
        text = (
            f"New reservation request {res.reference}\n\n"
            f"{res.customer_name}, party of {res.party_size}\n"
            f"When: {_local_text(res, zone)} ({restaurant.timezone})\n"
            f"Phone: {res.customer_phone}\n" + (f"Notes: {res.notes}\n" if res.notes else "")
        )
        ids = queue_staff_notifications(
            db, restaurant.id, "reservation_requested",
            f"New reservation {res.reference} - party of {res.party_size}", text)
        background.add_task(deliver, ids)
    except Exception:  # noqa: BLE001  (email problems never fail the reservation)
        db.rollback()
    return res


# --------------------------------------------------------------- owner side
def _view(res: Reservation, user: User) -> OwnerReservationOut:
    out = OwnerReservationOut.model_validate(res)
    out.allowed_next = reservation_allowed_next(res.status, user.role)
    return out


def _load(db: Session, restaurant: Restaurant, reservation_id: int) -> Reservation:
    res = db.scalar(
        select(Reservation).options(selectinload(Reservation.history))
        .where(Reservation.id == reservation_id, Reservation.restaurant_id == restaurant.id))
    if res is None:
        raise HTTPException(404, "Reservation not found")
    return res


@router.get("/reservations", response_model=list[OwnerReservationOut])
def list_reservations(
    user: User = Depends(get_current_user),
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(Reservation).options(selectinload(Reservation.history))
        .where(Reservation.restaurant_id == restaurant.id)
        .order_by(Reservation.id.desc()).limit(50)).all()
    return [_view(r, user) for r in rows]


@router.post("/reservations/{reservation_id}/status", response_model=OwnerReservationOut)
def change_reservation_status(
    reservation_id: int,
    body: ReservationStatusChangeIn,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: Session = Depends(get_db),
):
    res = _load(db, restaurant, reservation_id)
    target = body.status
    if target not in RESERVATION_ROLES:  # e.g. REQUESTED: not a step anyone can move a booking into
        raise HTTPException(409, f"A reservation cannot be changed to {target}")
    if user.role not in RESERVATION_ROLES.get(target, set()):
        raise HTTPException(403, "You do not have permission to make this change")
    if target not in reservation_next_statuses(res.status):
        raise HTTPException(409, f"A reservation that is {res.status} cannot be changed to {target}")
    note = (body.note or "").strip() or None
    if target in RESERVATION_REASON_REQUIRED and not note:
        raise HTTPException(422, "Please give a reason")

    res.status = target
    res.history.append(ReservationStatusHistory(
        restaurant_id=restaurant.id, status=target, changed_by_user_id=user.id, note=note))
    db.commit()

    # Tell the guest (only if they gave an email address)
    if res.customer_email and target in ("CONFIRMED", "DECLINED", "CANCELLED"):
        try:
            when = _local_text(res, _zone(restaurant.timezone))
            verb = {"CONFIRMED": "is confirmed", "DECLINED": "could not be accepted", "CANCELLED": "was cancelled"}[target]
            text = (f"Hello {res.customer_name},\n\nYour reservation {res.reference} at {restaurant.name} "
                    f"for {res.party_size} on {when} {verb}.\n" + (f"Note from the restaurant: {note}\n" if note else ""))
            nid = queue_notification(db, restaurant.id, "reservation_" + target.lower(),
                                     res.customer_email, f"Your reservation at {restaurant.name} {verb}", text)
            background.add_task(deliver, [nid])
        except Exception:  # noqa: BLE001
            db.rollback()
    return _view(res, user)


# ----------------------------------------------------------- notification log
@router.get(
    "/notifications", response_model=list[NotificationOut],
    dependencies=[Depends(require_roles(OWNER, MANAGER))],
)
def list_notifications(restaurant: Restaurant = Depends(get_current_restaurant), db: Session = Depends(get_db)):
    return db.scalars(
        select(Notification).where(Notification.restaurant_id == restaurant.id)
        .order_by(Notification.id.desc()).limit(20)).all()
