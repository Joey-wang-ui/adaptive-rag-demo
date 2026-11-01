"""Shared order repository and deterministic audit service for both entry points."""

import hashlib
import json
from pathlib import Path

from shipment_bot.models import Document
from shipment_bot.review import compare
from shipment_bot.store import Store, now


class AuditService:
    def __init__(self, path: Path):
        self.store = Store(path)
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS confirmed_orders (
                    order_id TEXT PRIMARY KEY, document TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_tasks (
                    task_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, source TEXT NOT NULL,
                    delivery TEXT NOT NULL, report TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)

    def confirm_order(self, order_id: str, document: Document):
        # Immutable IDs prevent a delayed group task auditing a silently changed order.
        if not order_id or document.reference_order_id != order_id or not document.customer:
            raise ValueError("确认订单必须包含匹配的订单号和客户")
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO confirmed_orders VALUES (?,?,?)",
                (order_id, document.model_dump_json(), now()),
            )

    def orders(self):
        with self.store.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM confirmed_orders ORDER BY order_id")]

    def audit(self, task_id: str, order_id: str, delivery: Document, source: str):
        raw = delivery.model_dump_json()
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM audit_tasks WHERE task_id=?", (task_id,)).fetchone()
            if old:
                if (old["order_id"], old["delivery"], old["source"]) != (order_id, raw, source):
                    raise ValueError("任务 ID 已被不同材料使用")
                return json.loads(old["report"])
            order = db.execute(
                "SELECT document FROM confirmed_orders WHERE order_id=?", (order_id,)
            ).fetchone()
            if not order:
                raise ValueError("未找到已确认订单")
            if delivery.reference_order_id != order_id or not delivery.customer:
                raise ValueError("出库单必须明确订单号和客户，禁止猜测关联")
            report = compare(Document.model_validate_json(order["document"]), delivery)
            db.execute(
                "INSERT INTO audit_tasks VALUES (?,?,?,?,?,?)",
                (task_id, order_id, source, raw, report.model_dump_json(), now()),
            )
            return report.model_dump(mode="json")

    def results(self):
        with self.store.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM audit_tasks ORDER BY created_at")]


def event_task_id(account: str, group: str, sender: str, message: str) -> str:
    return hashlib.sha256(json.dumps([account, group, sender, message]).encode()).hexdigest()
