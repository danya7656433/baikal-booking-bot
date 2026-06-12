from sqlalchemy import select

from db.models import Booking, PaymentTransaction
from db.session import get_session


PAYMENT_STATUSES = {
    "awaiting_payment",
    "partially_paid",
    "paid",
    "overpaid",
    "refunded",
}


def _require_booking(db_session, booking_id: int) -> Booking:
    booking = db_session.get(Booking, booking_id)
    if booking is None:
        raise LookupError(f"Booking #{booking_id} not found")
    return booking


def _positive_amount(amount: int) -> int:
    amount = int(amount)
    if amount <= 0:
        raise ValueError("Amount must be positive")
    return amount


def _total_for(booking: Booking) -> int:
    total = booking.calculated_total
    if total is None:
        total = booking.manual_total
    return max(int(total or 0), 0)


def _payment_status(total: int, net_paid: int, refunds: int) -> str:
    if refunds > 0 and net_paid == 0:
        return "refunded"
    if net_paid <= 0:
        return "awaiting_payment"
    if net_paid < total:
        return "partially_paid"
    if net_paid == total:
        return "paid"
    return "overpaid"


def _transactions(db_session, booking_id: int) -> list[PaymentTransaction]:
    return list(
        db_session.scalars(
            select(PaymentTransaction).where(
                PaymentTransaction.booking_id == booking_id
            )
        )
    )


def _balance_from(booking: Booking, transactions: list[PaymentTransaction]) -> dict:
    if transactions:
        paid = sum(
            transaction.amount
            for transaction in transactions
            if transaction.kind in {"payment", "adjustment"}
        )
        refunds = sum(
            transaction.amount
            for transaction in transactions
            if transaction.kind == "refund"
        )
    else:
        paid = max(int(booking.paid_amount or 0), 0)
        refunds = max(int(booking.refund_amount or 0), 0)

    total = _total_for(booking)
    net_paid = paid - refunds
    return {
        "total": total,
        "paid": paid,
        "refunds": refunds,
        "net_paid": net_paid,
        "remaining": max(total - net_paid, 0),
        "overpaid": max(net_paid - total, 0),
        "payment_status": _payment_status(total, net_paid, refunds),
    }


def get_payment_balance(db_session, booking_id: int) -> dict:
    booking = _require_booking(db_session, booking_id)
    return _balance_from(booking, _transactions(db_session, booking_id))


def preview_payment(db_session, booking_id: int, amount: int) -> dict:
    amount = _positive_amount(amount)
    balance = get_payment_balance(db_session, booking_id)
    net_paid = balance["net_paid"] + amount
    total = balance["total"]
    return {
        **balance,
        "paid": balance["paid"] + amount,
        "net_paid": net_paid,
        "remaining": max(total - net_paid, 0),
        "overpaid": max(net_paid - total, 0),
        "payment_status": _payment_status(total, net_paid, balance["refunds"]),
    }


def _ensure_legacy_history(
    db_session, booking: Booking, transactions: list[PaymentTransaction]
) -> None:
    if transactions:
        return

    legacy_paid = max(int(booking.paid_amount or 0), 0)
    legacy_refunds = max(int(booking.refund_amount or 0), 0)
    if legacy_paid:
        db_session.add(
            PaymentTransaction(
                booking_id=booking.id,
                amount=legacy_paid,
                kind="adjustment",
                payment_method="legacy",
                comment="Initial balance before payment history",
            )
        )
    if legacy_refunds:
        db_session.add(
            PaymentTransaction(
                booking_id=booking.id,
                amount=legacy_refunds,
                kind="refund",
                payment_method="legacy",
                comment="Initial refund before payment history",
            )
        )
    if legacy_paid or legacy_refunds:
        db_session.flush()


def _sync_booking(db_session, booking: Booking) -> dict:
    balance = _balance_from(booking, _transactions(db_session, booking.id))
    if balance["net_paid"] < 0:
        raise ValueError("Operation would make net paid amount negative")
    booking.paid_amount = balance["paid"]
    booking.refund_amount = balance["refunds"]
    booking.payment_status = balance["payment_status"]
    return balance


def _record_transaction(
    booking_id: int,
    amount: int,
    kind: str,
    method: str | None,
    admin_id: int,
    comment: str,
) -> dict:
    with get_session() as db_session:
        booking = _require_booking(db_session, booking_id)
        transactions = _transactions(db_session, booking_id)
        _ensure_legacy_history(db_session, booking, transactions)
        current = get_payment_balance(db_session, booking_id)

        if kind == "refund" and amount > current["net_paid"]:
            raise ValueError("Refund cannot exceed net paid amount")
        if kind == "adjustment" and current["paid"] + amount < 0:
            raise ValueError("Adjustment would make paid amount negative")

        db_session.add(
            PaymentTransaction(
                booking_id=booking_id,
                amount=amount,
                kind=kind,
                payment_method=method,
                admin_id=admin_id,
                comment=comment or None,
            )
        )
        db_session.flush()
        _sync_booking(db_session, booking)

    with get_session() as verification_session:
        return get_payment_balance(verification_session, booking_id)


def add_payment(
    booking_id: int,
    amount: int,
    method: str,
    admin_id: int,
    comment: str = "",
) -> dict:
    return _record_transaction(
        booking_id,
        _positive_amount(amount),
        "payment",
        method,
        admin_id,
        comment,
    )


def add_refund(
    booking_id: int,
    amount: int,
    method: str,
    admin_id: int,
    comment: str,
) -> dict:
    return _record_transaction(
        booking_id,
        _positive_amount(amount),
        "refund",
        method,
        admin_id,
        comment,
    )


def add_adjustment(
    booking_id: int,
    amount: int,
    admin_id: int,
    comment: str,
) -> dict:
    amount = int(amount)
    if amount == 0:
        raise ValueError("Adjustment amount must not be zero")
    if not comment.strip():
        raise ValueError("Adjustment comment is required")
    return _record_transaction(
        booking_id,
        amount,
        "adjustment",
        None,
        admin_id,
        comment,
    )
