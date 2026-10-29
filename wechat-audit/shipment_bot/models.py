"""Strict extraction contracts. Unknown values stay unknown."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Line(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=160)
    sku: str | None = Field(default=None, max_length=100)
    specification: str | None = Field(default=None, max_length=160)
    quantity: Decimal | None = Field(default=None, ge=0, le=Decimal("100000000"))
    unit: str | None = Field(default=None, max_length=30)
    unit_price: Decimal | None = Field(default=None, ge=0, le=Decimal("100000000"))
    price_unit: str | None = Field(default=None, max_length=30)
    amount: Decimal | None = Field(default=None, ge=0, le=Decimal("1000000000000"))
    evidence: str = Field(default="", max_length=2000)


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer: str | None = Field(default=None, max_length=160)
    reference_order_id: str | None = Field(default=None, max_length=100)
    document_number: str | None = Field(default=None, max_length=100)
    lines: list[Line] = Field(min_length=1, max_length=100)
    total: Decimal | None = Field(default=None, ge=0, le=Decimal("1000000000000"))
    uncertainties: list[str] = Field(default_factory=list, max_length=30)


class Finding(BaseModel):
    level: Literal["difference", "uncertain"]
    code: str
    message: str


class Review(BaseModel):
    status: Literal["differences", "needs_confirmation", "no_differences"]
    findings: list[Finding]


class Attachment(BaseModel):
    path: str = Field(max_length=4096)
    content_type: str = Field(default="", max_length=120)


class Incoming(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=250)
    sender_id: str = Field(min_length=1, max_length=250)
    message_id: str = Field(min_length=1, max_length=250)
    text: str = Field(default="", max_length=20000)
    attachments: list[Attachment] = Field(default_factory=list, max_length=4)
