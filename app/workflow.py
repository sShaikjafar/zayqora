"""Status lifecycles and who may perform each step (spec 16.1 and 16.3).

Pure functions with no database access, so the rules are easy to read and test.
The role rules below are this project's interpretation of the specification's
'illustrative' lifecycle and permission tables; change them here in one place.
"""

# ------------------------------------------------------------------- orders
ORDER_STATUS_ORDER = [
    "CONFIRMED", "PREPARING", "READY", "OUT_FOR_DELIVERY", "COMPLETED", "REJECTED", "CANCELLED",
]

ORDER_TRANSITIONS = {
    "PENDING": {"CONFIRMED", "REJECTED", "CANCELLED"},
    "CONFIRMED": {"PREPARING", "CANCELLED"},
    "PREPARING": {"READY", "CANCELLED"},
    "READY": {"OUT_FOR_DELIVERY", "COMPLETED"},
    "OUT_FOR_DELIVERY": {"COMPLETED"},
}

# target status -> roles allowed to move an order INTO that status
ORDER_ROLES = {
    "CONFIRMED": {"owner", "manager", "cashier"},
    "REJECTED": {"owner", "manager", "cashier"},
    "CANCELLED": {"owner", "manager"},
    "PREPARING": {"owner", "manager", "chef"},
    "READY": {"owner", "manager", "chef"},
    "OUT_FOR_DELIVERY": {"owner", "manager", "cashier", "rider"},
    "COMPLETED": {"owner", "manager", "cashier", "waiter", "rider"},
}

ORDER_REASON_REQUIRED = {"REJECTED", "CANCELLED"}


def order_next_statuses(status: str, fulfilment: str) -> list[str]:
    nxt = set(ORDER_TRANSITIONS.get(status, set()))
    if fulfilment != "delivery":
        nxt.discard("OUT_FOR_DELIVERY")  # only delivery orders go out with a rider
    elif status == "READY":
        nxt.discard("COMPLETED")  # a delivery order must go out for delivery first
    return [s for s in ORDER_STATUS_ORDER if s in nxt]


def order_allowed_next(status: str, fulfilment: str, role: str) -> list[str]:
    return [s for s in order_next_statuses(status, fulfilment) if role in ORDER_ROLES.get(s, set())]


# ------------------------------------------------------------- reservations
RESERVATION_STATUS_ORDER = ["CONFIRMED", "SEATED", "COMPLETED", "DECLINED", "CANCELLED", "NO_SHOW"]

RESERVATION_TRANSITIONS = {
    "REQUESTED": {"CONFIRMED", "DECLINED", "CANCELLED"},
    "CONFIRMED": {"SEATED", "CANCELLED", "NO_SHOW"},
    "SEATED": {"COMPLETED"},
}

RESERVATION_ROLES = {
    "CONFIRMED": {"owner", "manager", "cashier"},
    "DECLINED": {"owner", "manager", "cashier"},
    "CANCELLED": {"owner", "manager", "cashier"},
    "SEATED": {"owner", "manager", "cashier", "waiter"},
    "COMPLETED": {"owner", "manager", "waiter"},
    "NO_SHOW": {"owner", "manager", "waiter"},
}

RESERVATION_REASON_REQUIRED = {"DECLINED", "CANCELLED"}


def reservation_next_statuses(status: str) -> list[str]:
    nxt = RESERVATION_TRANSITIONS.get(status, set())
    return [s for s in RESERVATION_STATUS_ORDER if s in nxt]


def reservation_allowed_next(status: str, role: str) -> list[str]:
    return [s for s in reservation_next_statuses(status) if role in RESERVATION_ROLES.get(s, set())]
