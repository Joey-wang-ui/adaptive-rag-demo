"""Bounded group-only workflow. No free-form chat and no send inside this module."""

import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from shipment_bot.auto_review import Recognized, summarize
from shipment_bot.extract import ExtractionError
from shipment_bot.models import Document
from shipment_bot.review import compare, render

CASE = r"[A-Za-z0-9_-]{1,40}"


def command(text, nickname):
    text = unicodedata.normalize("NFKC", text).strip()
    nickname = unicodedata.normalize("NFKC", nickname).strip()
    match = re.fullmatch(rf"@{re.escape(nickname)}\s+审核\s+({CASE})", text)
    return match.group(1) if match else None


def automatic_request(text, nickname):
    text = unicodedata.normalize("NFKC", text).strip()
    nickname = unicodedata.normalize("NFKC", nickname).strip()
    return bool(
        re.fullmatch(
            rf"@{re.escape(nickname)}\s+(?:请)?(?:帮我)?(?:审核|审查|审单|检查|看看|看下|看一下)"
            r"(?:一下)?(?:这(?:张|份|些)?(?:单据|出货单|单)?|出货单|单据)?[。.!！?？]*",
            text,
        )
    )


def inline_request(text, nickname):
    """Explicit @ review on first/last line may carry the document in the same message."""
    lines = text.strip().splitlines()
    if len(lines) >= 2:
        if automatic_request(lines[0], nickname):
            return "\n".join(lines[1:]).strip()
        if automatic_request(lines[-1], nickname):
            return "\n".join(lines[:-1]).strip()
    return None


def looks_like_document(text):
    """Local candidate filter only; never calls a model for ordinary conversation."""
    text = unicodedata.normalize("NFKC", text)
    if not text.strip() or len(text) > 20000:
        return False
    number = r"[0-9一二三四五六七八九十百千万两]"
    return bool(
        re.search(
            number
            + r"[\d.一二三四五六七八九十百千万两]*\s*(?:箱|袋|件|盒|包|瓶|公斤|千克|kg|斤|吨|元)",
            text,
            re.I,
        )
        or (
            re.search(r"客户|订单|订货|出货|发货|合计|单价|金额|总计", text)
            and re.search(number, text)
        )
    )


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
    def __init__(
        self,
        path,
        group,
        account,
        nickname,
        extract,
        image_loader,
        clock=time.time,
        audit_options=None,
    ):
        if not group.endswith("@chatroom") or not account or not nickname.strip():
            raise ValueError("必须绑定真实群 ID、当前账号和机器人群昵称。")
        self.group, self.account, self.nickname = group, account, nickname
        self.extract, self.image_loader, self.clock = extract, image_loader, clock
        self.audit_options = audit_options or {}
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS pending(sender TEXT PRIMARY KEY, code TEXT, role TEXT, at REAL);
                CREATE TABLE IF NOT EXISTS materials(code TEXT, role TEXT, raw TEXT, parsed TEXT, at REAL,
                    PRIMARY KEY(code,role));
                CREATE TABLE IF NOT EXISTS auto_batches(sender TEXT PRIMARY KEY,batch TEXT,at REAL,reviewed INTEGER,overflow INTEGER);
                CREATE TABLE IF NOT EXISTS auto_photos(id TEXT PRIMARY KEY,sender TEXT,batch TEXT,raw TEXT,parsed TEXT);
            """)
            # Never attach a new-session image to yesterday's pending label.
            db.execute("DELETE FROM pending")
            db.execute("DELETE FROM auto_batches")
            db.execute("DELETE FROM auto_photos")

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
        if kind == "text" and automatic_request(text, self.nickname):
            return self.auto_audit(sender)
        inline = inline_request(text, self.nickname) if kind == "text" else None
        if inline:
            if len(inline) > 20000:
                return "本条文字超过20000字，请按订单分开发送后审核。"
            self.collect_material(sender, identity, {"_audit_text": inline})
            return self.auto_audit(sender)
        if kind == "image":
            self.collect_material(sender, identity, msg)
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
            if label[3]:
                self.collect_material(sender, identity, {"_audit_text": text})
            return None
        if kind == "text" and "@" not in text and looks_like_document(text):
            self.collect_material(sender, identity, {"_audit_text": text})
        with self.connect() as db:
            pending = db.execute(
                "SELECT code,role,at FROM pending WHERE sender=?", (sender,)
            ).fetchone()
            # The next message from this sender must be the labelled image.
            db.execute("DELETE FROM pending WHERE sender=?", (sender,))
            if pending and kind == "image" and self.clock() - pending[2] <= 120:
                self.save(db, pending[0], pending[1], {"image": msg})
        return None

    def collect_material(self, sender, identity, msg):
        with self.connect() as db:
            row = db.execute(
                "SELECT batch,at,reviewed,overflow FROM auto_batches WHERE sender=?", (sender,)
            ).fetchone()
            if not row or row[2] or self.clock() - row[1] > 120:
                db.execute("DELETE FROM auto_photos WHERE sender=?", (sender,))
                db.execute(
                    "INSERT OR REPLACE INTO auto_batches VALUES (?,?,?,0,0)",
                    (sender, identity, self.clock()),
                )
                batch = identity
            else:
                batch = row[0]
            count = db.execute(
                "SELECT count(*) FROM auto_photos WHERE batch=?", (batch,)
            ).fetchone()[0]
            if count >= 4:
                db.execute("UPDATE auto_batches SET overflow=1 WHERE sender=?", (sender,))
                return
            db.execute(
                "INSERT OR IGNORE INTO auto_photos VALUES (?,?,?,?,NULL)",
                (identity, sender, batch, json.dumps(msg, ensure_ascii=False)),
            )
            db.execute("UPDATE auto_batches SET at=? WHERE sender=?", (self.clock(), sender))

    def auto_audit(self, sender):
        with self.connect() as db:
            batches = db.execute(
                "SELECT sender,batch,overflow FROM auto_batches WHERE at>=?", (self.clock() - 600,)
            ).fetchall()
            own = [row for row in batches if row[0] == sender]
            selected = own or batches
            if len(selected) != 1:
                return (
                    "没有可审核的近期材料，请先发订单文字或单据图片再 @我 审核；也可在同一条消息首行写 @我 审核，下面粘贴正文。"
                    if not selected
                    else "近期有多人发材料，无法确定审核哪一单。请由发材料的人本人 @我 审核，避免串单。"
                )
            owner, batch, overflow = selected[0]
            if overflow:
                db.execute("UPDATE auto_batches SET reviewed=1 WHERE sender=?", (owner,))
                return "这批超过4份材料，未开始审核。请分成最多4份（文字和图片合计）一批重新发送，再 @我 审核。"
            rows = db.execute(
                "SELECT id,raw,parsed FROM auto_photos WHERE batch=? ORDER BY rowid", (batch,)
            ).fetchall()
            db.execute("UPDATE auto_batches SET reviewed=1 WHERE sender=?", (owner,))
        items, errors = [], []
        for i, (identity, raw, parsed) in enumerate(rows, 1):
            try:
                if parsed:
                    item = Recognized.model_validate_json(parsed)
                else:
                    material = json.loads(raw)
                    if "_audit_text" in material:
                        item = self.extract.auto_text(material["_audit_text"])
                    else:
                        path = self.image_loader(self.group, material)
                        item = self.extract.auto_image(path)
                    if len(item.document.lines) > 30:
                        raise ExtractionError("该份材料超过30行商品，请拆分为独立单据。")
                    with self.connect() as db:
                        db.execute(
                            "UPDATE auto_photos SET parsed=? WHERE id=?",
                            (item.model_dump_json(), identity),
                        )
                item.image_index = i
                items.append(item)
            except ExtractionError as error:
                errors.append(f"第{i}份材料未完成识别：{error}")
        return summarize(
            items,
            errors,
            bool(self.audit_options.get("require_customer", False)),
            bool(self.audit_options.get("require_specification", False)),
        )

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
