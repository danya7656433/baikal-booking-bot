from datetime import date
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Search(Input):
    date_from: date
    date_to: date
    adults: int = Field(default=2, ge=1, le=14)
    children: int = Field(default=0, ge=0, le=14)
    children_beds: int = Field(default=0, ge=0, le=14)

    @model_validator(mode="after")
    def validate_stay(self):
        if not 1 <= (self.date_to - self.date_from).days <= 31:
            raise ValueError("Проживание от 1 до 31 ночи")
        if self.children_beds > self.children or self.adults + self.children_beds > 14:
            raise ValueError("Проверьте количество гостей и детских мест")
        return self


class BookingInput(Search):
    room_type: str = Field(max_length=20)
    full_name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=10, max_length=25)
    comment: str = Field(default="", max_length=1500)
    rules_accepted: Literal[True]
    request_key: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9-]+$")
    quoted_total: int = Field(ge=1, le=10000000)

    @field_validator("phone")
    @classmethod
    def phone_digits(cls, value):
        if not re.fullmatch(r"\+?[\d ()-]+", value) or not 10 <= len(re.sub(r"\D", "", value)) <= 15:
            raise ValueError("Введите корректный номер телефона")
        return value


class Note(Input):
    note: str = Field(min_length=3, max_length=1500)


class Stage(Input):
    status: Literal["awaiting_payment", "cancelled", "rejected"]


class Money(Input):
    amount: int = Field(gt=0, le=10000000)
    note: str = Field(default="", max_length=1000)


class RoomEdit(Input):
    description: str = Field(min_length=3, max_length=2000)
    is_available: bool


class PriceEdit(Input):
    date_from: date
    date_to: date
    price: int = Field(ge=1, le=1000000)

    @model_validator(mode="after")
    def validate_range(self):
        if not 0 <= (self.date_to - self.date_from).days <= 365:
            raise ValueError("Период цен не должен превышать год")
        return self


class BlockEdit(Input):
    room_type: str = Field(max_length=20)
    date_from: date
    date_to: date
    reason: str = Field(min_length=3, max_length=200)

    @model_validator(mode="after")
    def validate_range(self):
        if not 1 <= (self.date_to - self.date_from).days <= 366:
            raise ValueError("Проверьте период блокировки")
        return self
