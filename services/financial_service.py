from typing import Iterable


def _money(value) -> int:
    return max(int(value or 0), 0)


def calculate_required_prepayment(booking, total: int) -> int:
    total = _money(total)
    value = _money(getattr(booking, "prepayment_value", 30))
    if getattr(booking, "prepayment_type", "percent") == "fixed":
        return min(value, total)
    percent = min(value, 100)
    return min(round(total * percent / 100), total)


def calculate_booking_balance(booking, calculated_total: int) -> dict[str, int]:
    base_total = getattr(booking, "manual_total", None)
    base_total = _money(calculated_total if base_total is None else base_total)
    discount = _money(getattr(booking, "discount_amount", 0))
    extra_services = _money(getattr(booking, "extra_services_amount", 0))
    paid = _money(getattr(booking, "paid_amount", 0))
    refunds = _money(getattr(booking, "refund_amount", 0))
    total = max(base_total - discount + extra_services, 0)
    net_paid = max(paid - refunds, 0)
    return {
        "base_total": base_total,
        "discount": discount,
        "extra_services": extra_services,
        "total": total,
        "paid": paid,
        "refunds": refunds,
        "net_paid": net_paid,
        "remaining": max(total - net_paid, 0),
        "required_prepayment": calculate_required_prepayment(booking, total),
    }


def apply_confirmed_payment(booking, paid_amount: int, calculated_total: int) -> dict[str, int]:
    booking.paid_amount = _money(paid_amount)
    booking.status = "paid"
    booking.payment_deadline = None
    return calculate_booking_balance(booking, calculated_total)


def financial_totals(bookings: Iterable, calculated_totals: Iterable[int]) -> dict[str, int]:
    result = {
        "billed": 0,
        "paid": 0,
        "refunds": 0,
        "net_paid": 0,
        "remaining": 0,
        "discounts": 0,
        "extra_services": 0,
    }
    for booking, calculated_total in zip(bookings, calculated_totals):
        balance = calculate_booking_balance(booking, calculated_total)
        result["billed"] += balance["total"]
        result["paid"] += balance["paid"]
        result["refunds"] += balance["refunds"]
        result["net_paid"] += balance["net_paid"]
        result["remaining"] += balance["remaining"]
        result["discounts"] += balance["discount"]
        result["extra_services"] += balance["extra_services"]
    return result
