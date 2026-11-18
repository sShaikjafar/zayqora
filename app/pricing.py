"""Pure pricing rules (spec 16.4). All amounts are integer minor units (cents/paise).

Order of application, stated explicitly:
  1. subtotal = sum of line totals (base price + chosen variant + add-ons) x quantity
  2. tax      = subtotal x tax rate, rounded half up. Tax is ADDED on top and applies to the
                subtotal only (not to the delivery fee).
  3. delivery fee is charged only for delivery orders.
  4. total    = subtotal + tax + delivery fee
"""


def tax_cents(subtotal_cents: int, tax_bps: int) -> int:
    # tax_bps = basis points: 500 means 5.00%
    return (subtotal_cents * tax_bps + 5000) // 10000


def compute_totals(subtotal_cents: int, tax_bps: int, delivery_fee_cents: int, fulfilment: str) -> dict:
    fee = delivery_fee_cents if fulfilment == "delivery" else 0
    tax = tax_cents(subtotal_cents, tax_bps)
    return {
        "subtotal_cents": subtotal_cents,
        "tax_cents": tax,
        "delivery_fee_cents": fee,
        "total_cents": subtotal_cents + tax + fee,
    }
