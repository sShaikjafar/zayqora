"""VIRELEO platform administration (spec 9.2). Every action here is written to the audit log."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_roles
from ..models import ACTIVE, OWNER, PLATFORM_ADMIN, SUSPENDED, AuditLog, Order, Restaurant, User
from ..schemas import AdminPlanIn, AdminRestaurantOut, AdminStatusIn, AuditOut

router = APIRouter(
    prefix="/api/v1/admin", tags=["platform admin"],
    dependencies=[Depends(require_roles(PLATFORM_ADMIN))],
)


def _row(db: Session, r: Restaurant) -> AdminRestaurantOut:
    owner = db.scalar(select(User.email).where(User.restaurant_id == r.id, User.role == OWNER).order_by(User.id))
    orders = db.scalar(select(func.count(Order.id)).where(Order.restaurant_id == r.id)) or 0
    return AdminRestaurantOut(
        id=r.id, name=r.name, slug=r.slug, plan=r.plan, status=r.status, created_at=r.created_at,
        owner_email=owner, order_count=orders)


@router.get("/restaurants", response_model=list[AdminRestaurantOut])
def list_restaurants(db: Session = Depends(get_db)):
    restaurants = db.scalars(select(Restaurant).order_by(Restaurant.id)).all()
    counts = dict(db.execute(select(Order.restaurant_id, func.count(Order.id)).group_by(Order.restaurant_id)).all())
    owners: dict[int, str] = {}
    for rid, email in db.execute(
        select(User.restaurant_id, User.email).where(User.role == OWNER).order_by(User.id)
    ).all():
        owners.setdefault(rid, email)
    return [
        AdminRestaurantOut(
            id=r.id, name=r.name, slug=r.slug, plan=r.plan, status=r.status, created_at=r.created_at,
            owner_email=owners.get(r.id), order_count=counts.get(r.id, 0))
        for r in restaurants
    ]


@router.post("/restaurants/{restaurant_id}/status", response_model=AdminRestaurantOut)
def set_status(
    restaurant_id: int, body: AdminStatusIn,
    admin: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise HTTPException(404, "Restaurant not found")
    if restaurant.status == body.status:
        raise HTTPException(409, f"This restaurant is already {body.status}")
    reason = (body.reason or "").strip() or None
    if body.status == SUSPENDED and not reason:
        raise HTTPException(422, "A reason is required to suspend a restaurant")
    restaurant.status = body.status
    action = "tenant.suspend" if body.status == SUSPENDED else "tenant.reactivate"
    db.add(AuditLog(actor_user_id=admin.id, restaurant_id=restaurant.id, action=action, detail=reason))
    db.commit()
    return _row(db, restaurant)


@router.post("/restaurants/{restaurant_id}/plan", response_model=AdminRestaurantOut)
def set_plan(
    restaurant_id: int, body: AdminPlanIn,
    admin: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise HTTPException(404, "Restaurant not found")
    old = restaurant.plan
    restaurant.plan = body.plan
    db.add(AuditLog(actor_user_id=admin.id, restaurant_id=restaurant.id, action="tenant.plan_change",
                    detail=f"{old} -> {body.plan}"))
    db.commit()
    return _row(db, restaurant)


@router.get("/audit-log", response_model=list[AuditOut])
def audit_log(db: Session = Depends(get_db)):
    rows = db.execute(
        select(AuditLog, User.email).join(User, User.id == AuditLog.actor_user_id, isouter=True)
        .order_by(AuditLog.id.desc()).limit(50)
    ).all()
    return [
        AuditOut(id=a.id, action=a.action, detail=a.detail, restaurant_id=a.restaurant_id,
                 actor_email=email, created_at=a.created_at)
        for a, email in rows
    ]
