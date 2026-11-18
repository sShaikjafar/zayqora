import csv
import io
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time as dtime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import get_current_restaurant, require_roles
from ..models import ACCOUNTANT, MANAGER, OWNER, Order, Reservation, Restaurant
from ..schemas import ReportDay, ReportItem, ReportOut, ReportRevenue
from .reservations import _zone

router = APIRouter(
    prefix="/api/v1/reports", tags=["reports"],
    dependencies=[Depends(require_roles(OWNER, MANAGER, ACCOUNTANT))],
)

NOT_SALES = {"REJECTED", "CANCELLED"}  # orders that never became business


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _range(restaurant: Restaurant, date_from: date | None, date_to: date | None):
    from fastapi import HTTPException

    zone = _zone(restaurant.timezone)
    end_day = date_to or datetime.now(zone).date()
    start_day = date_from or (end_day - timedelta(days=29))
    if start_day > end_day:
        raise HTTPException(422, "The start date must not be after the end date")
    if (end_day - start_day).days > 365:
        raise HTTPException(422, "Please choose a range of at most one year")
    start = datetime.combine(start_day, dtime.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(end_day + timedelta(days=1), dtime.min, tzinfo=zone).astimezone(timezone.utc)
    return zone, start_day, end_day, start, end


def _orders_in_range(db: Session, restaurant: Restaurant, start, end):
    return db.scalars(
        select(Order).options(selectinload(Order.items))
        .where(Order.restaurant_id == restaurant.id, Order.created_at >= start, Order.created_at < end)
        .order_by(Order.id)
    ).all()


@router.get("/summary", response_model=ReportOut)
def summary(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: Session = Depends(get_db),
):
    """Definitions: revenue counts COMPLETED orders only, by the day the order was placed, in the
    restaurant's timezone. 'orders' per day and top items exclude rejected and cancelled orders."""
    zone, d0, d1, start, end = _range(restaurant, date_from, date_to)
    orders = _orders_in_range(db, restaurant, start, end)

    by_status: Counter = Counter()
    days = {d0 + timedelta(days=i): [0, 0] for i in range((d1 - d0).days + 1)}
    items: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    sub = tax = fee = total = completed = 0

    for o in orders:
        by_status[o.status] += 1
        day = _aware(o.created_at).astimezone(zone).date()
        if o.status not in NOT_SALES:
            if day in days:
                days[day][0] += 1
            for it in o.items:
                items[it.name][0] += it.quantity
                items[it.name][1] += it.line_total_cents
        if o.status == "COMPLETED":
            completed += 1
            sub += o.subtotal_cents
            tax += o.tax_cents
            fee += o.delivery_fee_cents
            total += o.total_cents
            if day in days:
                days[day][1] += o.total_cents

    reservations = db.scalars(
        select(Reservation).where(
            Reservation.restaurant_id == restaurant.id,
            Reservation.starts_at >= start, Reservation.starts_at < end)
    ).all()
    res_status = Counter(r.status for r in reservations)

    top = sorted(items.items(), key=lambda kv: (-kv[1][0], kv[0]))[:10]
    return ReportOut(
        from_date=d0.isoformat(), to_date=d1.isoformat(), timezone=restaurant.timezone or "UTC",
        currency=restaurant.currency, orders_total=len(orders), orders_by_status=dict(by_status),
        revenue=ReportRevenue(
            completed_orders=completed, subtotal_cents=sub, tax_cents=tax, delivery_fee_cents=fee,
            total_cents=total, average_order_cents=(total // completed if completed else 0)),
        daily=[ReportDay(date=d.isoformat(), orders=v[0], revenue_cents=v[1]) for d, v in days.items()],
        top_items=[ReportItem(name=n, quantity=v[0], revenue_cents=v[1]) for n, v in top],
        reservations_total=len(reservations), reservations_by_status=dict(res_status))


def _safe(value) -> str:
    """Neutralise spreadsheet formula injection (a cell starting with = + - @ can run as a formula)."""
    text = "" if value is None else str(value)
    if text[:1] in ("=", "@", "\t", "\r") or (text[:1] in "+-" and not re.match(r"^[+-][\d\s(]", text)):
        return "'" + text
    return text


@router.get("/orders.csv")
def orders_csv(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: Session = Depends(get_db),
):
    zone, d0, d1, start, end = _range(restaurant, date_from, date_to)
    tz_name = restaurant.timezone or "UTC"
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "reference", f"created_local ({tz_name})", "status", "fulfilment", "customer_name", "customer_phone",
        "items", "subtotal", "tax", "delivery_fee", "total", "currency", "payment_method", "payment_status"])
    money = lambda c: f"{c / 100:.2f}"  # noqa: E731
    for o in _orders_in_range(db, restaurant, start, end):
        local = _aware(o.created_at).astimezone(zone).strftime("%Y-%m-%d %H:%M")
        summary_text = "; ".join(
            f"{i.quantity} x {i.name}" + (f" ({i.variant_name})" if i.variant_name else "") for i in o.items)
        writer.writerow([_safe(v) for v in [
            o.reference, local, o.status, o.fulfilment, o.customer_name, o.customer_phone, summary_text,
            money(o.subtotal_cents), money(o.tax_cents), money(o.delivery_fee_cents), money(o.total_cents),
            o.currency, o.payment_method, o.payment_status]])
    return Response(
        content="\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="orders_{d0}_{d1}.csv"'})
