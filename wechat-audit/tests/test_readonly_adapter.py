from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from shipment_bot.models import Document
from shipment_bot.wechat.readonly import Inbox, JsonlReplay, Message, ReviewProcessor
from shipment_bot.workbench import AuditService
from shipment_bot.workbench_api import create_app


def document(quantity="10", customer="甲客户"):
    return Document.model_validate(
        {
            "customer": customer,
            "reference_order_id": "A001",
            "lines": [
                {
                    "name": "蛋液",
                    "specification": "10kg",
                    "quantity": quantity,
                    "unit": "箱",
                    "unit_price": "85",
                    "price_unit": "箱",
                    "amount": "850",
                }
            ],
            "total": "850",
        }
    )


def message(**updates):
    fields = dict(
        account_id="bot",
        group_id="test-group",
        sender_id="employee",
        message_id="m1",
        sent_at=datetime(2026, 11, 1, tzinfo=UTC),
        kind="image",
        order_id="A001",
        image_path="/not-yet-downloaded.png",
    )
    fields.update(updates)
    return Message(**fields)


def trigger(**updates):
    fields = dict(
        message_id="trigger",
        kind="text",
        text="/审核 A001",
        image_path=None,
        sent_at=datetime(2026, 11, 1, tzinfo=UTC) + timedelta(seconds=10),
    )
    fields.update(updates)
    return message(**fields)


def test_persist_restart_and_duplicate_replay(tmp_path):
    events = [message(), trigger()]
    replay = tmp_path / "events.jsonl"
    replay.write_text("\n".join(m.model_dump_json() for m in events), encoding="utf-8")
    path = tmp_path / "audit.db"
    for _ in range(2):
        inbox = Inbox(path, "bot", {"test-group"})
        inbox.run(JsonlReplay(replay))
        assert inbox.messages() == events
    assert inbox.candidates(events[1], "bot")[1] == [events[0]]


def test_burst_and_whitelist(tmp_path):
    inbox = Inbox(tmp_path / "a.db", "bot", {"test-group"})
    assert not inbox.accept(message(group_id="other"))
    assert not inbox.accept(message(account_id="other"))
    for i in range(100):
        assert inbox.accept(message(message_id=str(i)))
        assert not inbox.accept(message(message_id=str(i)))
    assert len(inbox.messages()) == 100
    with pytest.raises(ValueError):
        Inbox(tmp_path / "b.db", "bot", set())
    with pytest.raises(ValueError):
        Inbox(tmp_path / "wildcard.db", "bot", {"*"})
    with pytest.raises(ValueError):
        inbox.accept(message(message_id="0", text="changed"))


@pytest.mark.parametrize(
    "changes",
    [
        dict(sender_id="other"),
        dict(group_id="other"),
        dict(order_id="B002"),
        dict(order_id=None),
        dict(sent_at=datetime(2026, 10, 31, tzinfo=UTC)),
        dict(sent_at=datetime(2026, 11, 2, tzinfo=UTC)),
    ],
)
def test_never_associate_other_sender_order_group_or_stale_image(tmp_path, changes):
    inbox = Inbox(tmp_path / "a.db", "bot", {"test-group", "other"})
    inbox.accept(message(**changes))
    t = trigger()
    inbox.accept(t)
    with pytest.raises(ValueError):
        inbox.candidates(t, "bot")


@pytest.mark.parametrize(
    "text,mentions,allowed",
    [
        ("@Bot 审核 A001", None, False),
        ("审核", ["bot"], True),
        ("审核", ["other"], False),
        ("/审核 A001", None, True),
        ("/审核 A001 extra", None, False),
    ],
)
def test_real_mention_or_exact_command(tmp_path, text, mentions, allowed):
    inbox = Inbox(tmp_path / "a.db", "bot", {"test-group"})
    inbox.accept(message())
    t = trigger(text=text, mention_ids=mentions)
    inbox.accept(t)
    if allowed:
        assert inbox.candidates(t, "bot")[0] == "A001"
    else:
        with pytest.raises(ValueError):
            inbox.candidates(t, "bot")


def test_api_and_wechat_share_orders_engine_and_results(tmp_path):
    service = AuditService(tmp_path / "shared.db")
    token = "local-test-token-" * 3
    app = create_app(service, token)
    with TestClient(app) as client:
        assert client.get("/orders").status_code == 401
        client.headers["Authorization"] = "Bearer " + token
        assert (
            client.post(
                "/orders", json={"order_id": "A001", "document": document().model_dump(mode="json")}
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/orders", json={"order_id": "A001", "document": document().model_dump(mode="json")}
            ).status_code
            == 409
        )
        body = dict(
            task_id="manual", order_id="A001", delivery=document("9").model_dump(mode="json")
        )
        response = client.post("/audits", json=body)
        assert response.status_code == 200
        assert response.json()["status"] == "differences"
        assert any(f["code"] == "quantity" for f in response.json()["findings"])
        assert client.post("/audits", json=body).json() == response.json()
        body["delivery"] = document().model_dump(mode="json")
        assert client.post("/audits", json=body).status_code == 409
        image = tmp_path / "bill.png"
        Image.new("RGB", (20, 20)).save(image)
        inbox = Inbox(tmp_path / "shared.db", "bot", {"test-group"})
        inbox.accept(message(image_path=str(image)))
        t = trigger()
        inbox.accept(t)

        class Extractor:
            def image(self, path):
                assert path == image
                return document("9")

        worker = ReviewProcessor(inbox, service, [tmp_path], Extractor())
        assert worker.process(t, "bot") == response.json()
        assert worker.process(t, "bot") == response.json()
        assert len(client.get("/audits").json()) == 2
        restored = AuditService(tmp_path / "shared.db")
        assert len(restored.orders()) == 1
        assert len(restored.results()) == 2


def test_customer_mismatch_and_missing_identity(tmp_path):
    service = AuditService(tmp_path / "a.db")
    service.confirm_order("A001", document())
    report = service.audit("mismatch", "A001", document(customer="乙客户"), "workbench")
    assert report["status"] == "differences"
    assert any(f["code"] == "customer" for f in report["findings"])
    missing = document().model_copy(update={"reference_order_id": None})
    with pytest.raises(ValueError):
        service.audit("missing", "A001", missing, "wechat")


def test_photo_api_reuses_extraction_and_blocks_path_escape(tmp_path):
    image_dir = tmp_path / "uploads"
    image_dir.mkdir()
    image = image_dir / "bill.png"
    Image.new("RGB", (20, 20)).save(image)

    class Extractor:
        def image(self, path):
            return document()

    service = AuditService(tmp_path / "a.db")
    service.confirm_order("A001", document())
    with TestClient(
        create_app(service, "x" * 32, extractor=Extractor(), media_roots=[image_dir])
    ) as client:
        client.headers["Authorization"] = "Bearer " + "x" * 32
        payload = {"task_id": "photo", "order_id": "A001", "image_path": str(image)}
        assert client.post("/audits/photo", json=payload).json()["status"] == "no_differences"
        payload["image_path"] = str(tmp_path / "outside.png")
        Image.new("RGB", (20, 20)).save(payload["image_path"])
        assert client.post("/audits/photo", json=payload).status_code == 409


def test_timestamp_and_unpersisted_trigger_fail_closed(tmp_path):
    with pytest.raises(ValueError):
        message(sent_at=datetime(2026, 11, 1))
    inbox = Inbox(tmp_path / "a.db", "bot", {"test-group"})
    inbox.accept(message())
    with pytest.raises(ValueError):
        inbox.candidates(trigger(), "bot")


def test_internal_api_client_and_disabled_reply():
    import httpx
    from shipment_bot.wechat.audit_client import LocalAuditClient
    from shipment_bot.wechat.replies import DisabledReplies

    def handler(request):
        assert request.url.path == "/ingress/wechat/audits"
        assert request.headers["Authorization"] == "Bearer " + "x" * 32
        return httpx.Response(200, json={"status": "no_differences", "findings": []})

    client = LocalAuditClient(
        "http://127.0.0.1:8766", "x" * 32, transport=httpx.MockTransport(handler)
    )
    assert client.audit("t1", "A001", document(), "wechat")["status"] == "no_differences"
    with pytest.raises(ValueError):
        LocalAuditClient("http://external.example", "x" * 32)
    with pytest.raises(RuntimeError):
        DisabledReplies().send("group", "report", "t1")
