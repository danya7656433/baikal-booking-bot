import io
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from cachetools import TTLCache
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from config import ADMIN_CHAT_ID, BOT_TOKEN, HOTEL_ADDRESS, ADMIN_CONTACT, LOCATION_LINK, PAYMENT_DEADLINE_HOURS
from database import init_settings
from db import Booking, BlockedDate, BookingSeason, BookingYear, Room, RoomPriceOverride, get_session
from services.inventory_service import ROOM_NAMES, is_available, room_parts, rooms_overlap
from services.payment_service import record_transaction_in_session
from services.paths import DATA_DIR
from webapp.auth import admin_user, create_session, current_user, validate_telegram, validate_widget
from webapp.models import Receipt, WebSession
from webapp.schemas import BookingInput, Search, Note, Stage, Money, RoomEdit, PriceEdit, BlockEdit
from webapp.service import ROOT, booking_for, catalog, create_booking, notify, options, record_change, snapshot, today

MAX_UPLOAD = 8 * 1024 * 1024
UserDep = Annotated[dict, Depends(current_user)]
AdminDep = Annotated[dict, Depends(admin_user)]


class TelegramLogin(BaseModel):
    init_data: str = Field(min_length=1, max_length=16384)


class SeasonInput(BaseModel):
    year: int = Field(ge=2026, le=2100)
    is_active: bool


def create_app(*, demo=False):
    @asynccontextmanager
    async def lifespan(app):
        init_settings()
        yield

    app = FastAPI(title="Дача на Байкале", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.demo = demo
    app.state.admin_ids = {int(value) for value in (os.getenv("ADMIN_USER_IDS") or str(ADMIN_CHAT_ID)).split(",") if value.strip()}
    if demo:
        app.state.admin_ids = {1}
    hosts = [host.strip() for host in os.getenv("WEBAPP_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    limits = TTLCache(maxsize=10000, ttl=60)

    @app.middleware("http")
    async def safety_headers(request, call_next):
        if request.url.path.startswith("/api/"):
            length = request.headers.get("content-length", "0")
            if not length.isdigit() or int(length) > MAX_UPLOAD + 65536:
                return JSONResponse({"detail": "Файл слишком большой. Максимум 8 МБ"}, status_code=413)
            ip = request.client.host if request.client else "unknown"
            kind = "auth" if request.url.path.startswith("/api/auth/") else "api"
            key = (ip, kind)
            count = limits.get(key, 0)
            if count >= (30 if kind == "auth" else 180):
                return JSONResponse({"detail": "Слишком много запросов. Повторите через минуту"}, status_code=429, headers={"Retry-After": "60"})
            limits[key] = count + 1
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(IntegrityError)
    async def conflict_handler(request, error):
        logging.warning("Database constraint rejected web request: %s", type(error).__name__)
        return JSONResponse({"detail": "Данные изменились или даты уже заняты. Обновите страницу"}, status_code=409)

    @app.exception_handler(ValueError)
    async def value_handler(request, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, error):
        messages = [f"{'.'.join(str(part) for part in item['loc'][1:])}: {item['msg']}" for item in error.errors()]
        return JSONResponse({"detail": "; ".join(messages)}, status_code=422)

    @app.get("/api/health")
    def health():
        with get_session() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.get("/api/config")
    def configuration():
        with get_session() as session:
            year = session.query(BookingYear).first()
            season = session.query(BookingSeason).first()
            return {"name": "Дача на Байкале", "address": HOTEL_ADDRESS, "contact": ADMIN_CONTACT,
                "map_url": LOCATION_LINK, "today": today().isoformat(), "booking_year": year.year,
                "season_active": season.is_active, "demo": demo,
                "bot_username": os.getenv("BOT_USERNAME", "").lstrip("@"),
                "payment_instructions": os.getenv("PAYMENT_INSTRUCTIONS") or "Реквизиты для перевода предоставит администратор после подтверждения заявки.",
                "rooms": catalog(session)}

    @app.post("/api/auth/telegram")
    def telegram_login(payload: TelegramLogin):
        return {"token": create_session(validate_telegram(payload.init_data, BOT_TOKEN or ""))}

    @app.post("/api/auth/widget")
    def widget_login(payload: dict):
        return {"token": create_session(validate_widget(payload, BOT_TOKEN or ""))}

    @app.post("/api/auth/demo")
    def demo_login(request: Request, role: str = "guest"):
        if not demo or not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(404)
        if role not in {"guest", "admin"}:
            raise HTTPException(422)
        return {"token": create_session({"id": 1 if role == "admin" else 100, "first_name": "Администратор" if role == "admin" else "Гость", "username": "demo"})}

    @app.get("/api/me")
    def me(user: UserDep):
        return {key: value for key, value in user.items() if key != "token_hash"}

    @app.post("/api/auth/logout")
    def logout(user: UserDep):
        with get_session() as session:
            session.query(WebSession).filter_by(token_hash=user["token_hash"]).delete()
        return {"ok": True}

    @app.post("/api/availability")
    def availability(payload: Search):
        with get_session() as session:
            return {"options": options(session, payload)}

    @app.get("/api/calendar")
    def calendar(room_type: str, month: date):
        room_parts(room_type)
        if month.day != 1 or abs(month.year - today().year) > 2:
            raise HTTPException(422, "Некорректный месяц")
        end = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        with get_session() as session:
            return {"days": [{"date": (month + timedelta(days=i)).isoformat(),
                "available": is_available(session, room_type, month + timedelta(days=i), month + timedelta(days=i + 1))}
                for i in range((end - month).days)]}

    @app.post("/api/bookings", status_code=201)
    def reserve(payload: BookingInput, user: UserDep):
        return create_booking(payload, user)

    @app.get("/api/bookings")
    def bookings(user: UserDep):
        with get_session() as session:
            rows = session.query(Booking).filter_by(user_id=user["id"], deleted_at=None).order_by(Booking.id.desc()).limit(100).all()
            return [snapshot(session, row) for row in rows]

    @app.get("/api/bookings/{booking_id}")
    def booking_detail(booking_id: int, user: UserDep):
        with get_session() as session:
            return snapshot(session, booking_for(session, booking_id, user), user["is_admin"])

    @app.post("/api/bookings/{booking_id}/cancel")
    def cancel(booking_id: int, payload: Note, user: UserDep):
        with get_session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            booking = booking_for(session, booking_id, user)
            if booking.status not in {"new", "pending", "awaiting_payment", "awaiting_payment_confirmation", "paid"}:
                raise HTTPException(409, "Эту заявку уже нельзя отменить")
            previous = booking.status
            booking.status = "awaiting_cancellation"
            booking.payment_deadline = None
            booking.comment = f"Причина отмены: {payload.note}\n{booking.comment or ''}"
            record_change(session, booking, user["id"], "cancellation_requested", previous, payload.note)
            notify(session, ADMIN_CHAT_ID, f"Запрос на отмену заявки #{booking.id}: {payload.note}")
            return snapshot(session, booking)

    @app.post("/api/bookings/{booking_id}/receipts", status_code=201)
    async def upload_receipt(booking_id: int, user: UserDep, file: UploadFile = File(...)):
        raw = await file.read(MAX_UPLOAD + 1)
        await file.close()
        if len(raw) > MAX_UPLOAD:
            raise HTTPException(413, "Максимальный размер чека 8 МБ")
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > 16000000:
                    raise ValueError("Нужна фотография JPEG, PNG или WEBP до 16 мегапикселей")
                image.load()
                image.thumbnail((2400, 2400))
                cleaned = io.BytesIO()
                image.convert("RGB").save(cleaned, format="JPEG", quality=90)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
            raise HTTPException(422, "Не удалось прочитать изображение чека") from error
        destination = DATA_DIR / "receipts" / (secrets.token_hex(20) + ".jpg")
        try:
            with get_session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                booking = booking_for(session, booking_id, user)
                if booking.status not in {"awaiting_payment", "paid"}:
                    raise HTTPException(409, "Чек можно отправить после подтверждения заявки")
                if session.query(Receipt).filter_by(booking_id=booking_id, status="pending").first():
                    raise HTTPException(409, "Чек уже ожидает проверки")
                if snapshot(session, booking)["remaining"] <= 0:
                    raise HTTPException(409, "Заявка уже полностью оплачена")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(cleaned.getvalue())
                receipt = Receipt(booking_id=booking_id, file_name=destination.name)
                session.add(receipt)
                booking.status = "awaiting_payment_confirmation"
                booking.payment_deadline = None
                session.flush()
                record_change(session, booking, user["id"], "receipt_uploaded")
                notify(session, ADMIN_CHAT_ID, f"Новый чек по заявке #{booking_id}. Требуется проверка в webapp.")
                return snapshot(session, booking)
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    @app.get("/api/receipts/{receipt_id}/image")
    def receipt_image(receipt_id: int, user: UserDep):
        with get_session() as session:
            receipt = session.get(Receipt, receipt_id)
            if not receipt:
                raise HTTPException(404)
            booking_for(session, receipt.booking_id, user)
            path = DATA_DIR / "receipts" / Path(receipt.file_name).name
            if not path.is_file():
                raise HTTPException(404, "Изображение не найдено")
            return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/admin/bookings")
    def admin_bookings(user: AdminDep, q: str = Query(default="", max_length=120), status: str = "", offset: int = Query(default=0, ge=0)):
        with get_session() as session:
            rows = session.query(Booking).filter_by(deleted_at=None)
            if status:
                rows = rows.filter_by(status=status)
            if q:
                value = q.replace("%", "\\%").replace("_", "\\_")
                rows = rows.filter(Booking.full_name.ilike(f"%{value}%", escape="\\") | Booking.phone.ilike(f"%{value}%", escape="\\") | (Booking.id == (int(q) if q.isdigit() else -1)))
            total = rows.count()
            return {"total": total, "bookings": [snapshot(session, row, True) for row in rows.order_by(Booking.date_from.desc(), Booking.id.desc()).offset(offset).limit(50)]}

    @app.post("/api/admin/bookings/{booking_id}/status")
    def change_status(booking_id: int, payload: Stage, user: AdminDep):
        with get_session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            booking = booking_for(session, booking_id, user)
            if booking.status == payload.status:
                return snapshot(session, booking, True)
            allowed = {"awaiting_payment": {"new", "pending"}, "rejected": {"new", "pending"},
                "cancelled": {"new", "pending", "awaiting_cancellation", "awaiting_payment"}}
            if booking.status not in allowed[payload.status]:
                raise HTTPException(409, "Этот переход статуса недоступен")
            previous = booking.status
            booking.status = payload.status
            booking.payment_deadline = datetime.now() + timedelta(hours=PAYMENT_DEADLINE_HOURS) if payload.status == "awaiting_payment" else None
            record_change(session, booking, user["id"], "status_changed", previous, payload.status)
            notify(session, booking.user_id, f"Заявка #{booking.id}: статус изменён. Подробности в разделе «Мои брони».")
            return snapshot(session, booking, True)

    @app.post("/api/admin/receipts/{receipt_id}/approve")
    def approve_receipt(receipt_id: int, payload: Money, user: AdminDep):
        with get_session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            receipt = session.get(Receipt, receipt_id)
            if not receipt:
                raise HTTPException(404)
            booking = booking_for(session, receipt.booking_id, user)
            if receipt.status == "approved":
                if receipt.amount != payload.amount:
                    raise HTTPException(409, "Этот чек уже подтверждён с другой суммой")
                return snapshot(session, booking, True)
            if receipt.status != "pending" or booking.status != "awaiting_payment_confirmation":
                raise HTTPException(409, "Чек или заявка уже изменились")
            balance = record_transaction_in_session(session, booking.id, payload.amount, "payment", "Перевод", user["id"], payload.note)
            receipt.status, receipt.amount, receipt.note = "approved", payload.amount, payload.note
            receipt.reviewed_at = datetime.utcnow()
            minimum = snapshot(session, booking)["required_prepayment"]
            booking.status = "paid" if balance["net_paid"] >= minimum else "awaiting_payment"
            booking.payment_deadline = None if booking.status == "paid" else datetime.now() + timedelta(hours=PAYMENT_DEADLINE_HOURS)
            record_change(session, booking, user["id"], "receipt_approved", after={"receipt_id": receipt_id, "amount": payload.amount})
            notify(session, booking.user_id, f"Оплата {payload.amount} руб. по заявке #{booking.id} подтверждена.")
            return snapshot(session, booking, True)

    @app.post("/api/admin/receipts/{receipt_id}/reject")
    def reject_receipt(receipt_id: int, payload: Note, user: AdminDep):
        with get_session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            receipt = session.get(Receipt, receipt_id)
            if not receipt or receipt.status != "pending":
                raise HTTPException(409, "Чек уже обработан")
            booking = booking_for(session, receipt.booking_id, user)
            if booking.status != "awaiting_payment_confirmation":
                raise HTTPException(409, "Статус заявки изменился")
            receipt.status, receipt.note, receipt.reviewed_at = "rejected", payload.note, datetime.utcnow()
            booking.status = "awaiting_payment"
            booking.payment_deadline = datetime.now() + timedelta(hours=PAYMENT_DEADLINE_HOURS)
            record_change(session, booking, user["id"], "receipt_rejected", after=payload.note)
            notify(session, booking.user_id, f"Чек по заявке #{booking.id} отклонён: {payload.note}")
            return snapshot(session, booking, True)

    @app.post("/api/admin/bookings/{booking_id}/refund")
    def refund(booking_id: int, payload: Money, user: AdminDep):
        if not payload.note:
            raise HTTPException(422, "Укажите основание возврата")
        with get_session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            booking = booking_for(session, booking_id, user)
            record_transaction_in_session(session, booking_id, payload.amount, "refund", "Перевод", user["id"], payload.note)
            record_change(session, booking, user["id"], "refund_recorded", after={"amount": payload.amount})
            return snapshot(session, booking, True)

    @app.patch("/api/admin/rooms/{room_type}")
    def edit_room(room_type: str, payload: RoomEdit, user: AdminDep):
        if room_type not in ROOM_NAMES:
            raise HTTPException(404)
        with get_session() as session:
            room = session.query(Room).filter_by(name=ROOM_NAMES[room_type]).one()
            room.description, room.is_available = payload.description, payload.is_available
        return {"ok": True}

    @app.post("/api/admin/prices")
    def prices(payload: PriceEdit, user: AdminDep):
        with get_session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            session.query(RoomPriceOverride).filter(RoomPriceOverride.date >= payload.date_from, RoomPriceOverride.date <= payload.date_to).delete()
            for day in range((payload.date_to - payload.date_from).days + 1):
                session.add(RoomPriceOverride(date=payload.date_from + timedelta(days=day), price=payload.price))
        return {"ok": True}

    @app.get("/api/admin/prices")
    def list_prices(user: AdminDep):
        with get_session() as session:
            return [{"date": row.date.isoformat(), "price": row.price} for row in session.query(RoomPriceOverride).order_by(RoomPriceOverride.date).limit(1000)]

    @app.put("/api/admin/season")
    def season(payload: SeasonInput, user: AdminDep):
        with get_session() as session:
            session.query(BookingYear).first().year = payload.year
            session.query(BookingSeason).first().is_active = payload.is_active
        return {"ok": True}

    @app.get("/api/admin/blocks")
    def list_blocks(user: AdminDep):
        with get_session() as session:
            return [{"id": row.id, "room_type": row.room_type, "date": row.date.isoformat(), "reason": row.reason}
                for row in session.query(BlockedDate).filter(BlockedDate.date >= today()).order_by(BlockedDate.date).limit(1000)]

    @app.post("/api/admin/blocks")
    def block_dates(payload: BlockEdit, user: AdminDep):
        room_parts(payload.room_type)
        with get_session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            if not is_available(session, payload.room_type, payload.date_from, payload.date_to):
                raise HTTPException(409, "В этом периоде уже есть бронь или блокировка")
            for day in range((payload.date_to - payload.date_from).days):
                session.add(BlockedDate(room_type=payload.room_type, date=payload.date_from + timedelta(days=day), reason=payload.reason, created_by=user["id"]))
        return {"ok": True}

    @app.delete("/api/admin/blocks/{block_id}")
    def unblock(block_id: int, user: AdminDep):
        with get_session() as session:
            row = session.get(BlockedDate, block_id)
            if not row:
                raise HTTPException(404)
            session.delete(row)
        return {"ok": True}

    app.mount("/photos", StaticFiles(directory=ROOT / "photos"), name="photos")
    dist = ROOT / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
