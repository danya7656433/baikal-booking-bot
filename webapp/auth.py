import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qsl

from aiogram.utils.web_app import safe_parse_webapp_init_data
from fastapi import HTTPException, Request

from db import get_session
from webapp.models import WebSession


def validate_telegram(init_data: str, bot_token: str) -> dict:
    try:
        if len(init_data) > 16384 or not bot_token:
            raise ValueError("Invalid data")
        pairs = parse_qsl(init_data, strict_parsing=True)
        if len(dict(pairs)) != len(pairs):
            raise ValueError("Duplicate fields")
        data = safe_parse_webapp_init_data(bot_token, init_data)
        age = time.time() - data.auth_date.timestamp()
        if not data.user or not -30 <= age <= 3600:
            raise ValueError("Expired authorization")
        return {"id": data.user.id, "first_name": data.user.first_name, "username": data.user.username}
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(401, "Вход в Telegram истёк или недействителен") from error


def validate_widget(data: dict, bot_token: str) -> dict:
    try:
        if not bot_token:
            raise ValueError("Bot is not configured")
        signature = data.get("hash", "")
        values = {key: str(value) for key, value in data.items() if key != "hash" and value is not None}
        check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
        expected = hmac.new(hashlib.sha256(bot_token.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("Invalid signature")
        if not -30 <= time.time() - int(data["auth_date"]) <= 3600 or int(data["id"]) <= 0:
            raise ValueError("Expired authorization")
        return {"id": int(data["id"]), "first_name": str(data["first_name"]), "username": data.get("username")}
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(401, "Не удалось подтвердить вход через Telegram") from error


def create_session(user: dict) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    with get_session() as session:
        session.query(WebSession).filter(WebSession.expires_at < now).delete()
        session.add(WebSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
            user_id=user["id"], name=user["first_name"], username=user.get("username"),
            expires_at=now + timedelta(hours=12)))
    return token


def current_user(request: Request) -> dict:
    raw = request.headers.get("Authorization", "")
    if not raw.startswith("Bearer ") or len(raw) > 200:
        raise HTTPException(401, "Войдите, чтобы продолжить")
    token_hash = hashlib.sha256(raw[7:].encode()).hexdigest()
    with get_session() as session:
        record = session.get(WebSession, token_hash)
        if not record or record.expires_at < datetime.utcnow():
            raise HTTPException(401, "Войдите, чтобы продолжить")
        return {"id": record.user_id, "name": record.name, "username": record.username,
            "is_admin": record.user_id in request.app.state.admin_ids, "token_hash": token_hash}


def admin_user(request: Request) -> dict:
    user = current_user(request)
    if not user["is_admin"]:
        raise HTTPException(403, "Доступ только для администратора")
    return user
