"""Bounded group-only workflow. No free-form chat and no send inside this module."""

import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from shipment_bot.extract import ExtractionError
from shipment_bot.models import Document
from shipment_bot.review import compare, render

CASE = r"[A-Za-z0-9_-]{1,40}"


def command(text, nickname):
    text = unicodedata.normalize("NFKC", text).strip()
    nickname = unicodedata.normalize("NFKC", nickname).strip()
    match = re.fullmatch(rf"@{re.escape(nickname)}\s+审核\s+({CASE})", text)
    return match.group(1) if match else None


def resolve_group(db, name):
    hits = {
        row["username"]
        for row in db.search_contact(name)
        if name in (row.get("nick_name"), row.get("remark"))
    }
    if len(hits) != 1 or not next(iter(hits)).endswith("@chatroom"):
        raise ValueError("未找到唯一同名群。请给测试群取唯一名称，并先在电脑微信打开该群。")
    return hits.pop()


class GroupAudit:
    def __init__(self, path, group, account, nickname, extract, image_loader, clock=time.time):
        if not group.endswith("@chatroom") or not account or not nickname.strip():
            raise ValueError("必须绑定真实群 ID、当前账号和机器人群昵称。")
        self.group, self.account, self.nickname = group, account, nickname
        self.extract, self.image_loader, self.clock = extract, image_loader, clock
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS pending(sender TEXT PRIMARY KEY, code TEXT, role TEXT, at REAL);
                CREATE TABLE IF NOT EXISTS materials(code TEXT, role TEXT, raw TEXT, parsed TEXT, at REAL,
                    PRIMARY KEY(code,role));
            """)
            # Never attach a new-session image to yesterday's pending label.
            db.execute("DELETE FROM pending")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def handle(self, group, msg):
        sender = str(msg.get("sender_username") or "")
        if group != self.group or not sender or sender == self.account:
            return None
        if not msg.get("local_id") or not msg.get("sort_seq"):
            return None
        identity = hashlib.sha256(
            json.dumps(
                [self.account, group, msg.get("shard"), msg["local_id"], msg["sort_seq"]]
            ).encode()
        ).hexdigest()
        with self.connect() as db:
            if db.execute("SELECT 1 FROM seen WHERE id=?", (identity,)).fetchone():
                return None
            # Claim before processing/sending. Failed sends are NOT automatically replayed.
            db.execute("INSERT INTO seen VALUES (?)", (identity,))
        text = str(msg.get("content") or "").strip()
        # WeChatDB 1.2.8.1 uses Chinese type labels and may retain the sender prefix.
        for prefix in (sender + ":\n", sender + ":\r\n"):
            if text.startswith(prefix):
                text = text[len(prefix) :]
                break
        kind = {"文本": "text", "图片": "image"}.get(msg.get("type"), msg.get("type"))
        code = command(text, self.nickname) if kind == "text" else None
        if code:
            try:
                return self.audit(code)
            except ExtractionError as error:
                return f"{code} 待确认：{error}\n本次未完成核对，请人工核实后发货。"
        label = re.fullmatch(rf"(订单|出货单)\s+({CASE})(?:\n([\s\S]+))?", text)
        if kind == "text" and label:
            role = "order" if label[1] == "订单" else "delivery"
            with self.connect() as db:
                db.execute("DELETE FROM materials WHERE code=? AND role=?", (label[2], role))
                db.execute("DELETE FROM pending WHERE code=? AND role=?", (label[2], role))
                db.execute("DELETE FROM pending WHERE sender=?", (sender,))
                if label[3]:
                    self.save(db, label[2], role, {"text": label[3]})
                else:
                    db.execute(
                        "INSERT INTO pending VALUES (?,?,?,?)",
                        (sender, label[2], role, self.clock()),
                    )
            return None
        with self.connect() as db:
            pending = db.execute(
                "SELECT code,role,at FROM pending WHERE sender=?", (sender,)
            ).fetchone()
            # The next message from this sender must be the labelled image.
            db.execute("DELETE FROM pending WHERE sender=?", (sender,))
            if pending and kind == "image" and self.clock() - pending[2] <= 120:
                self.save(db, pending[0], pending[1], {"image": msg})
        return None

    def save(self, db, code, role, raw):
        db.execute(
            "INSERT OR REPLACE INTO materials VALUES (?,?,?,NULL,?)",
            (code, role, json.dumps(raw, ensure_ascii=False), self.clock()),
        )

    def audit(self, code):
        with self.connect() as db:
            rows = db.execute(
                "SELECT role,raw,parsed,at FROM materials WHERE code=?", (code,)
            ).fetchall()
        if len(rows) != 2:
            return f"{code} 材料不足。请分别发送“订单 {code}”＋图片、“出货单 {code}”＋图片，再 @我 审核 {code}。"
        documents = {}
        for role, raw_json, parsed, at in rows:
            if self.clock() - at > 86400:
                raise ExtractionError("材料已超过24小时，请重新发送本次最终确认的两份单据。")
            if parsed:
                documents[role] = Document.model_validate_json(parsed)
                continue
            raw = json.loads(raw_json)
            if "image" in raw:
                path = self.image_loader(self.group, raw["image"])
                document = self.extract.image(path)
            else:
                document = self.extract.parse(raw["text"])
            if len(document.lines) > 30:
                raise ExtractionError("每单最多30行，请拆分为独立订单。")
            documents[role] = document
            with self.connect() as db:
                db.execute(
                    "UPDATE materials SET parsed=? WHERE code=? AND role=? AND raw=?",
                    (document.model_dump_json(), code, role, raw_json),
                )
        result = compare(documents["order"], documents["delivery"])
        report = render(code, int(max(row[3] for row in rows)), result)
        # The group adapter does not implement or accept approval commands.
        return "\n".join(
            line
            for line in report.splitlines()
            if not line.startswith("确认原图和实物后，可发送：")
        )
