from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint

from db.models import Base


class WebSession(Base):
    __tablename__ = "web_sessions"
    token_hash = Column(String, primary_key=True)
    user_id = Column(Integer, nullable=False)
    name = Column(String, nullable=False)
    username = Column(String)
    expires_at = Column(DateTime, nullable=False)


class BookingRequest(Base):
    __tablename__ = "web_booking_requests"
    __table_args__ = (UniqueConstraint("user_id", "request_key"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    request_key = Column(String, nullable=False)
    payload_hash = Column(String, nullable=False)
    booking_id = Column(Integer, nullable=False)


class Receipt(Base):
    __tablename__ = "web_receipts"
    id = Column(Integer, primary_key=True)
    booking_id = Column(Integer, nullable=False, index=True)
    file_name = Column(String, nullable=False)
    status = Column(String, default="pending", nullable=False)
    amount = Column(Integer)
    note = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime)


class Outbox(Base):
    __tablename__ = "web_outbox"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, nullable=False)
    text = Column(String, nullable=False)
    sent_at = Column(DateTime)
    attempts = Column(Integer, default=0)
    next_attempt_at = Column(DateTime, default=datetime.utcnow)
