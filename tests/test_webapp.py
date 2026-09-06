import hashlib
import hmac
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

os.environ.setdefault("ADMIN_CHAT_ID", "1")

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Booking, BookingSeason, BookingYear, PaymentTransaction, Room
from db.inventory_guards import install_inventory_guards
from services.inventory_service import ROOM_NAMES, room_capacity
from tests.helpers import session_context
from webapp.app import create_app
from webapp.auth import create_session, validate_telegram, validate_widget
from webapp.models import Receipt
from webapp.service import today


class WebAppTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name).as_posix()}/test.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            install_inventory_guards(connection)
        self.Session = sessionmaker(bind=self.engine)
        self.start = today() + timedelta(days=2)
        self.end = self.start + timedelta(days=2)
        with self.Session.begin() as session:
            session.add(BookingYear(year=self.start.year))
            session.add(BookingSeason(is_active=True))
            for code, name in ROOM_NAMES.items():
                session.add(Room(name=name, capacity=room_capacity(code), description="Room", is_available=True))
        self.stack = ExitStack()
        for module in ("webapp.app", "webapp.auth", "webapp.service"):
            self.stack.enter_context(patch(f"{module}.get_session", session_context(self.Session)))
        self.stack.enter_context(patch("webapp.app.init_settings", lambda: None))
        self.stack.enter_context(patch("webapp.app.DATA_DIR", Path(self.temp.name)))
        self.stack.enter_context(patch.dict(os.environ, {"WEBAPP_ALLOWED_HOSTS": "testserver", "ADMIN_USER_IDS": "1"}))
        self.client = self.stack.enter_context(TestClient(create_app()))
        self.guest = {"Authorization": "Bearer " + create_session({"id": 100, "first_name": "Guest"})}
        self.other = {"Authorization": "Bearer " + create_session({"id": 200, "first_name": "Other"})}
        self.admin = {"Authorization": "Bearer " + create_session({"id": 1, "first_name": "Admin"})}
        self.payload = {"date_from": self.start.isoformat(), "date_to": self.end.isoformat(), "adults": 2,
            "children": 0, "children_beds": 0, "room_type": "2", "full_name": "Test Guest", "phone": "+79000000000",
            "comment": "", "rules_accepted": True, "request_key": "request-key-000001", "quoted_total": 6000}

    def tearDown(self):
        self.stack.close()
        self.engine.dispose()
        self.temp.cleanup()

    def create(self, **updates):
        response = self.client.post("/api/bookings", headers=self.guest, json={**self.payload, **updates})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def confirm(self, booking_id):
        response = self.client.post(f"/api/admin/bookings/{booking_id}/status", headers=self.admin, json={"status": "awaiting_payment"})
        self.assertEqual(response.status_code, 200, response.text)

    def receipt(self, booking_id):
        image = io.BytesIO()
        Image.new("RGB", (100, 100), "white").save(image, "PNG")
        response = self.client.post(f"/api/bookings/{booking_id}/receipts", headers=self.guest, files={"file": ("receipt.png", image.getvalue(), "image/png")})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["receipts"][0]["id"]

    def test_booking_payment_lifecycle_and_receipt_idempotency(self):
        booking = self.create()
        self.confirm(booking["id"])
        receipt_id = self.receipt(booking["id"])
        path = f"/api/admin/receipts/{receipt_id}/approve"
        for _ in range(2):
            result = self.client.post(path, headers=self.admin, json={"amount": 3000, "note": "Received"})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["status"], "paid")
            self.assertEqual(result.json()["remaining"], 3000)
        with self.Session() as session:
            self.assertEqual(session.query(PaymentTransaction).count(), 1)
        wrong = self.client.post(path, headers=self.admin, json={"amount": 4000})
        self.assertEqual(wrong.status_code, 409)

    def test_booking_retry_and_conflicting_request(self):
        one = self.create()
        self.assertEqual(self.create()["id"], one["id"])
        response = self.client.post("/api/bookings", headers=self.guest, json={**self.payload, "request_key": "request-key-000002"})
        self.assertEqual(response.status_code, 409)
        response = self.client.post("/api/bookings", headers=self.guest, json={**self.payload, "full_name": "Different"})
        self.assertEqual(response.status_code, 409)

    def test_owner_and_admin_permissions(self):
        booking = self.create()
        self.assertEqual(self.client.get(f"/api/bookings/{booking['id']}", headers=self.other).status_code, 404)
        self.assertEqual(self.client.post(f"/api/bookings/{booking['id']}/cancel", headers=self.other, json={"note": "Test cancellation"}).status_code, 404)
        self.assertEqual(self.client.get("/api/admin/bookings", headers=self.guest).status_code, 403)
        self.assertEqual(self.client.get("/api/bookings").status_code, 401)
        self.assertEqual(self.client.post("/api/auth/demo").status_code, 404)

    def test_receipt_privacy_and_invalid_upload(self):
        booking = self.create()
        self.confirm(booking["id"])
        receipt_id = self.receipt(booking["id"])
        self.assertEqual(self.client.get(f"/api/receipts/{receipt_id}/image", headers=self.other).status_code, 404)
        self.assertEqual(self.client.get(f"/api/receipts/{receipt_id}/image", headers=self.guest).headers["content-type"], "image/jpeg")
        response = self.client.post(f"/api/bookings/{booking['id']}/receipts", headers=self.guest, files={"file": ("evil.png", b"<script>alert(1)</script>", "image/png")})
        self.assertEqual(response.status_code, 422)

    def test_price_changes_do_not_change_saved_booking(self):
        booking = self.create()
        response = self.client.post("/api/admin/prices", headers=self.admin, json={"date_from": self.start.isoformat(), "date_to": self.end.isoformat(), "price": 2500})
        self.assertEqual(response.status_code, 200)
        response = self.client.get(f"/api/bookings/{booking['id']}", headers=self.guest)
        self.assertEqual(response.json()["total"], 6000)
        response = self.client.post("/api/bookings", headers=self.guest, json={**self.payload, "request_key": "request-key-000003", "room_type": "5"})
        self.assertEqual(response.status_code, 409)

    def test_invalid_input_and_closed_season(self):
        response = self.client.post("/api/bookings", headers=self.guest, json={**self.payload, "adults": 3})
        self.assertEqual(response.status_code, 422)
        response = self.client.post("/api/bookings", headers=self.guest, json={**self.payload, "rules_accepted": False})
        self.assertEqual(response.status_code, 422)
        response = self.client.put("/api/admin/season", headers=self.admin, json={"year": self.start.year, "is_active": False})
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/api/bookings", headers=self.guest, json=self.payload)
        self.assertEqual(response.status_code, 409)

    def test_cancellation_does_not_release_inventory_before_approval(self):
        booking = self.create()
        response = self.client.post(f"/api/bookings/{booking['id']}/cancel", headers=self.guest, json={"note": "Plans changed"})
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/api/bookings", headers=self.other, json={**self.payload, "request_key": "request-key-000005"})
        self.assertEqual(response.status_code, 409)

    def test_blocking_dates_cannot_invalidate_existing_booking(self):
        self.create()
        response = self.client.post("/api/admin/blocks", headers=self.admin, json={"room_type": "all", "date_from": self.start.isoformat(), "date_to": self.end.isoformat(), "reason": "Maintenance"})
        self.assertEqual(response.status_code, 409)

    def test_logout_invalidates_session(self):
        self.assertEqual(self.client.post("/api/auth/logout", headers=self.guest).status_code, 200)
        self.assertEqual(self.client.get("/api/me", headers=self.guest).status_code, 401)


class TelegramAuthTest(unittest.TestCase):
    token = "123456:test-token"

    def init_data(self, age=0):
        data = {"auth_date": str(int(time.time()) - age), "query_id": "test", "user": json.dumps({"id": 123, "first_name": "Guest"})}
        check = "\n".join(f"{key}={data[key]}" for key in sorted(data))
        secret = hmac.new(b"WebAppData", self.token.encode(), hashlib.sha256).digest()
        data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        return urlencode(data)

    def test_valid_signed_telegram_data(self):
        self.assertEqual(validate_telegram(self.init_data(), self.token)["id"], 123)

    def test_tampered_expired_and_duplicate_data_are_rejected(self):
        from fastapi import HTTPException
        for value in (self.init_data().replace("Guest", "Attacker"), self.init_data(age=7200), self.init_data() + "&auth_date=1"):
            with self.assertRaises(HTTPException):
                validate_telegram(value, self.token)

    def test_browser_widget_signature(self):
        data = {"id": 123, "first_name": "Guest", "auth_date": int(time.time())}
        check = "\n".join(f"{key}={data[key]}" for key in sorted(data))
        data["hash"] = hmac.new(hashlib.sha256(self.token.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(validate_widget(data, self.token)["id"], 123)
