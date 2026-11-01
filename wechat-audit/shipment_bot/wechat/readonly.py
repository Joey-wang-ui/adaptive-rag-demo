"""Durable ingress contract; intentionally no sending or Windows automation API."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shipment_bot.models import Document
from shipment_bot.store import Store, now
from shipment_bot.workbench import AuditService, event_task_id


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=250)
    group_id: str = Field(min_length=1, max_length=250)
    sender_id: str = Field(min_length=1, max_length=250)
    message_id: str = Field(min_length=1, max_length=250)
    sent_at: datetime
    kind: Literal["text", "image"]
    text: str = Field(default="", max_length=20000)
    # Only provider-confirmed stable IDs, never display-name or visible @ text inference.
    mention_ids: list[str] | None = None
    order_id: str | None = None
    image_path: str | None = None

    @field_validator("sent_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        return value


class ReadOnlySource(Protocol):
    """Vendor binding must replay from durable state and supply stable IDs.

    Implement only after reviewing the actual wx4auto distribution/license/API.
    An implementation must not scan all groups or expose sending methods.
    """

    def listen(self, group_ids: frozenset[str], on_message): ...


class Inbox:
    def __init__(self, path: Path, account_id: str, groups: set[str]):
        if not account_id or not groups or any(not g.strip() or "*" in g for g in groups):
            raise ValueError("account and nonempty exact group whitelist required")
        self.account_id, self.groups = account_id, frozenset(groups)
        self.store = Store(path)
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS wechat_inbox (
                    event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, received_at TEXT NOT NULL
                );
            """)

    def accept(self, message: Message) -> bool:
        if message.account_id != self.account_id or message.group_id not in self.groups:
            return False
        key = event_task_id(
            message.account_id, message.group_id, message.sender_id, message.message_id
        )
        with self.store.connection() as db:
            existing = db.execute(
                "SELECT payload FROM wechat_inbox WHERE event_id=?", (key,)
            ).fetchone()
            payload = message.model_dump_json()
            if existing:
                if existing["payload"] != payload:
                    raise ValueError("same message identity has conflicting payload")
                return False
            db.execute("INSERT INTO wechat_inbox VALUES (?,?,?)", (key, payload, now()))
        return True

    def messages(self):
        with self.store.connection() as db:
            return [
                Message.model_validate_json(r["payload"])
                for r in db.execute("SELECT payload FROM wechat_inbox ORDER BY rowid")
            ]

    def run(self, source: ReadOnlySource):
        source.listen(self.groups, self.accept)

    def candidates(self, trigger: Message, bot_id: str, *, window_seconds=300):
        # An exact command works without a trusted @ event; visible @昵称 is never evidence.
        match = re.fullmatch(r"/审核 ([A-Za-z0-9_-]{1,40})", trigger.text.strip())
        mentioned = bot_id and bot_id in (trigger.mention_ids or [])
        order_id = match.group(1) if match else trigger.order_id if mentioned else None
        if not order_id or (trigger.order_id and trigger.order_id != order_id):
            raise ValueError("需要真实 @ 加明确订单号，或命令 /审核 订单号")
        stored = self.messages()
        if trigger not in stored:
            raise ValueError("trigger must be persisted in this whitelisted inbox")
        # Every candidate needs the same explicit order, sender, group and account.
        # Never pair the most recent unlabelled image or another employee's image.
        result = [
            m
            for m in stored
            if (m.account_id, m.group_id, m.sender_id, m.order_id)
            == (trigger.account_id, trigger.group_id, trigger.sender_id, order_id)
            and 0 <= (trigger.sent_at - m.sent_at).total_seconds() <= window_seconds
            and m.kind == "image"
        ]
        if not 1 <= len(result) <= 4:
            raise ValueError("需要 1～4 张明确关联同一订单的图片；请人工整理")
        return order_id, result


class ReviewProcessor:
    """Optional stage-two worker; never runs as part of read-only collection."""

    def __init__(self, inbox: Inbox, service: AuditService, media_roots: list[Path], extractor):
        self.inbox, self.service = inbox, service
        self.media_roots, self.extractor = media_roots, extractor

    def process(self, trigger: Message, bot_id: str):
        from shipment_bot.extract import validate_image

        order_id, images = self.inbox.candidates(trigger, bot_id)
        # Multi-page merging requires explicit document semantics; fail closed for now.
        if len(images) != 1:
            raise ValueError("当前引擎入口要求单张完整出库单，多页请在工作台整理")
        if not images[0].image_path:
            raise ValueError("图片尚未取得，保留任务等待重试")
        path = validate_image(Path(images[0].image_path), self.media_roots)
        delivery: Document = self.extractor.image(path)
        return self.service.audit(
            event_task_id(
                trigger.account_id, trigger.group_id, trigger.sender_id, trigger.message_id
            ),
            order_id,
            delivery,
            "wechat",
        )


class JsonlReplay:
    """Deterministic acceptance harness, not a claim about live wx4auto behavior."""

    def __init__(self, path: Path):
        self.path = path

    def listen(self, group_ids, on_message):
        with self.path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    message = Message.model_validate(json.loads(line))
                    if message.group_id in group_ids:
                        on_message(message)
