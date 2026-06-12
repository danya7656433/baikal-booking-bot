# Booking Core Reliability Design

## Goal

Implement the selected improvements without destabilizing the existing Telegram bot:

1. Remove global SQLAlchemy session usage from every touched scenario.
2. Add an immutable payment transaction history.
3. Add an admin action that appends a payment instead of replacing the total paid.
4. Add automatic and manually correctable stay statuses.
5. Use one booking card formatter for guest and admin views.
6. Warn about overpayments.
7. Include remaining balances in reminders.
8. Allow admin date changes with automatic availability checks and price recalculation.
9. Allow admin accommodation changes with automatic availability checks and price recalculation.
10. Send a daily admin summary at 09:00 Asia/Irkutsk.
11. Group repeated critical errors and report their counts every 30 minutes.
12. Add unit, integration, and end-to-end tests for every changed workflow.

Before implementation, create a full project archive and a timestamped database backup.

## Delivery Strategy

Use an incremental core replacement. Existing working handlers remain available while the
business logic they use is moved into focused services. Every changed write workflow uses
`get_session()` and commits through a short transaction. The backward-compatible global
`session` remains temporarily for untouched legacy paths, but no new or modified feature may
depend on it.

## Data Model

### Booking

Add:

- `calculated_total`: current calculated booking price.
- `payment_status`: `awaiting_payment`, `partially_paid`, `paid`, `overpaid`, or `refunded`.
- `stay_status`: `awaiting_checkin`, `checked_in`, `checked_out`, or `completed`.
- `stay_status_changed_at`: last automatic or manual stay-status transition.

Keep the existing `status` column during the transition. A compatibility mapper updates it
where old handlers still require it, but new financial and stay logic must not derive state
from the legacy column.

### PaymentTransaction

Immutable ledger entry:

- `id`
- `booking_id`
- `amount`
- `kind`: `payment`, `refund`, or `adjustment`
- `payment_method`
- `admin_id`
- `comment`
- `created_at`

Positive payments increase the paid balance. Refunds reduce the net paid balance. Adjustments
are explicit corrections and must include a comment.

### BookingChange

Audit entry:

- `booking_id`
- `actor_id`
- `kind`
- `before_json`
- `after_json`
- `created_at`

It records date changes, accommodation changes, recalculations, and manual stay-status changes.

### ErrorEvent

Persistent error aggregation:

- `fingerprint`
- `message`
- `traceback_preview`
- `first_seen_at`
- `last_seen_at`
- `total_count`
- `reported_count`
- `last_summary_at`

The fingerprint is based on exception type, normalized message, and the top application stack
frame.

## Services

### Payment Service

Responsibilities:

- Append payments, refunds, and adjustments in a short transaction.
- Calculate paid, refunded, net paid, remaining, and overpaid totals from the ledger.
- Derive `payment_status`.
- Prevent negative amounts and invalid refunds.
- Warn before accepting an overpayment.
- Verify persisted values using a fresh session before reporting success.

Existing `paid_amount` remains as a compatibility cache and is updated from the ledger after
each transaction.

### Booking Management Service

Responsibilities:

- Change dates after validating availability.
- Change accommodation after validating availability.
- Recalculate price using the pricing service.
- Keep all existing payment transactions.
- Return the new remaining balance or overpayment.
- Write a `BookingChange` audit record.
- Invalidate booked-date caches only after a successful commit.

### Stay Service

Uses timezone `Asia/Irkutsk`.

Automatic transitions:

- Confirmed active booking starts as `awaiting_checkin`.
- At 14:00 on `date_from`: `checked_in`.
- At 12:00 on `date_to`: `checked_out`.
- After the review request is logged: `completed`.

Admin may manually correct any stay status. Manual changes create a `BookingChange` record.
The automatic scheduler must not immediately undo a valid manual correction unless the next
chronological transition is due.

### Booking Card Service

Produces a shared structured booking view used by guest and admin formatters. It includes:

- Guest and accommodation details.
- Dates and people count.
- Current calculated total.
- Paid, refunded, remaining, and overpaid amounts.
- Payment status with emoji.
- Stay status with emoji.
- Admin-only notes and actions where applicable.

No router calculates financial totals or status labels independently.

### Notification Service

Idempotent notifications are logged in `NotificationLog`.

- Reminders at 7, 3, and 1 day before arrival to guest and admin.
- Arrival-day summary at 09:00.
- Departure-day reminder at 09:00.
- Every message includes the remaining balance when greater than zero.
- Review request after departure and stay completion.
- Restarting the bot must not duplicate already-sent notifications.

### Daily Summary Service

At 09:00 Asia/Irkutsk, send the administrator:

- Today's arrivals and departures.
- Bookings with remaining balances.
- Payment-deadline problems.
- Current checked-in guests.
- New and repeated critical error counts from the previous day.

### Error Monitor Service

- Send the first occurrence of a critical error immediately.
- Store and count matching repetitions.
- Send a grouped repeat summary every 30 minutes.
- Avoid sending identical tracebacks repeatedly.

## Admin Workflows

### Add Payment

Admin opens a booking card and selects `➕ Добавить оплату`.

1. Bot asks for amount.
2. Bot asks for payment method or offers common method buttons.
3. Service previews new paid, remaining, or overpaid totals.
4. If overpaid, admin must explicitly confirm.
5. Payment transaction is committed and verified.
6. Guest is notified when linked to Telegram.

The existing `Изменить предоплату` becomes an explicit correction action, not the normal payment
workflow.

### Change Dates

1. Admin selects a booking.
2. Calendar shows available and unavailable dates for the current accommodation.
3. Admin selects start and end dates.
4. Bot previews availability, new total, remaining balance, or overpayment.
5. Admin confirms.
6. Change commits atomically and notifications are sent.

### Change Accommodation

1. Admin selects a booking.
2. Bot offers valid accommodation combinations for the guest count and duration.
3. Unavailable options are disabled or clearly marked.
4. Bot previews new total and balance.
5. Admin confirms.
6. Change commits atomically and notifications are sent.

## Error Handling

- A user-facing success message is sent only after a fresh-session persistence check.
- Database exceptions roll back the short transaction and leave the booking unchanged.
- Telegram notification failures do not roll back already committed business data; they are
  logged and retried where appropriate.
- Invalid date or accommodation changes show a clear reason and preserve the current booking.

## Testing

Use a temporary SQLite database and a fake Telegram transport.

### Unit Tests

- Payment ledger totals, partial payment, full payment, refund, correction, and overpayment.
- Payment and stay status derivation.
- Price recalculation after date and accommodation changes.
- Stay transition times in Asia/Irkutsk.
- Error fingerprinting and grouping.
- Daily summary composition.
- Unified card formatting.

### Integration Tests

- Migrations create new tables and preserve existing bookings.
- Short transactions persist across fresh sessions.
- Add-payment workflow updates ledger, compatibility fields, card, and guest notification.
- Date change validates availability and keeps payment history.
- Accommodation change validates availability and keeps payment history.
- Reminder and review tasks remain idempotent after restart.
- Manual stay-status correction is audited.

### End-to-End Bot Tests

Test every button and FSM path affected by the project:

1. Guest booking creation.
2. Admin confirmation.
3. Screenshot payment confirmation.
4. Partial payment display.
5. Admin-added second payment.
6. Overpayment confirmation.
7. Date change.
8. Accommodation change.
9. Reminder with remaining balance.
10. Automatic check-in.
11. Automatic checkout.
12. Review request and completion.
13. Daily summary.
14. First critical error and grouped repeats.
15. Bot restart without duplicate notifications.

The final verification also runs compilation for every Python file, all existing tests, all new
tests, import checks, and a deployment smoke test on Bothost.

## Backup And Deployment

Before implementation:

- Create a timestamped full-project ZIP excluding generated caches and prior deployment archives.
- Create a timestamped database backup.
- Verify both artifacts can be opened.

Deployment is incremental. After each migration or workflow group:

- Run the relevant tests.
- Push to GitHub.
- Update Bothost.
- Confirm the bot reaches `Работает`.
- Inspect logs and perform the matching Telegram smoke test.
