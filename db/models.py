from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, unique=True)
    rules_accepted = Column(Boolean, default=False)
    username = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class Room(Base):
    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    capacity = Column(Integer)
    price = Column(Integer)
    description = Column(String, default="No description")
    photo_id = Column(String, nullable=True)
    is_available = Column(Boolean, default=True)


class RoomPriceOverride(Base):
    __tablename__ = "room_price_overrides"

    id = Column(Integer, primary_key=True)
    date = Column(Date)
    price = Column(Integer)
    created_at = Column(DateTime, default=datetime.now)


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    username = Column(String)
    full_name = Column(String)
    phone = Column(String)
    comment = Column(String)
    date_from = Column(Date)
    date_to = Column(Date)
    room_type = Column(String)
    adults = Column(Integer)
    children = Column(Integer, default=0)
    status = Column(String, default="new")
    children_beds = Column(String, nullable=True)
    created_at = Column(
        String, default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    payment_deadline = Column(DateTime, nullable=True)
    admin_comment = Column(String, nullable=True)
    manual_total = Column(Integer, nullable=True)
    paid_amount = Column(Integer, nullable=True)
    payment_method = Column(String, nullable=True)
    prepayment_type = Column(String, default="percent")
    prepayment_value = Column(Integer, default=30)
    discount_amount = Column(Integer, default=0)
    extra_services_amount = Column(Integer, default=0)
    refund_amount = Column(Integer, default=0)


class BookingDraft(Base):
    __tablename__ = "booking_drafts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, unique=True)
    data = Column(String)
    updated_at = Column(DateTime, default=datetime.now)


class BlockedDate(Base):
    __tablename__ = "blocked_dates"

    id = Column(Integer, primary_key=True)
    room_type = Column(String)
    date = Column(Date)
    reason = Column(String, nullable=True)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class GuestNote(Base):
    __tablename__ = "guest_notes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    admin_id = Column(Integer, nullable=True)
    note = Column(String)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)


class BookingHistory(Base):
    __tablename__ = "booking_history"

    id = Column(Integer, primary_key=True)
    booking_id = Column(Integer)
    actor_id = Column(Integer, nullable=True)
    action = Column(String)
    before = Column(String, nullable=True)
    after = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class UpdateLog(Base):
    __tablename__ = "update_logs"

    id = Column(Integer, primary_key=True)
    admin_id = Column(Integer)
    file_name = Column(String)
    version = Column(String, nullable=True)
    status = Column(String)
    timestamp = Column(
        String, default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    error_message = Column(String, nullable=True)


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True)
    booking_id = Column(Integer)
    user_id = Column(Integer)
    rating = Column(Integer)
    comment = Column(String)
    created_at = Column(
        String, default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )


class AdminLog(Base):
    __tablename__ = "admin_logs"

    id = Column(Integer, primary_key=True)
    admin_id = Column(Integer)
    action = Column(String)
    booking_id = Column(Integer, nullable=True)
    timestamp = Column(
        String, default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    booking_id = Column(Integer, nullable=True)
    message = Column(String)
    from_admin = Column(Boolean)
    timestamp = Column(
        String, default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id = Column(Integer, primary_key=True)
    booking_id = Column(Integer)
    kind = Column(String)
    days_before = Column(Integer, nullable=True)
    sent_at = Column(DateTime, default=datetime.now)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    booking_id = Column(Integer, nullable=True)
    user_message_id = Column(Integer, nullable=True)
    status = Column(String, default="open")
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    closed_at = Column(DateTime, nullable=True)


class BookingYear(Base):
    __tablename__ = "booking_year"

    id = Column(Integer, primary_key=True)
    year = Column(Integer, default=2025)


class BookingSeason(Base):
    __tablename__ = "booking_season"

    id = Column(Integer, primary_key=True)
    is_active = Column(Boolean, default=True)


class News(Base):
    __tablename__ = "news"

    id = Column(Integer, primary_key=True)
    admin_id = Column(Integer)
    content = Column(String)
    photo_id = Column(String, nullable=True)
    status = Column(String, default="draft")
    created_at = Column(
        String, default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    sent_at = Column(String, nullable=True)
    video_id = Column(String, nullable=True)
