import httpx
import pytest
from PIL import Image
from shipment_bot.extract import ExtractionError, Extractor, validate_image
from shipment_bot.models import Incoming
from shipment_bot.server import create_app
from shipment_bot.service import Bot
from shipment_bot.store import Store, scope
from test_review import doc


class StubExtractor:
    def __init__(self):
        self.calls = 0
        self.document = doc()

    def parse(self, text, warnings=None):
        self.calls += 1
        return self.document

    def image(self, path):
        self.calls += 1
        return self.document


@pytest.fixture
def setup(tmp_path):
    extractor = StubExtractor()
    store = Store(tmp_path / "audit.sqlite3")
    return Bot(store, extractor, [tmp_path / "media"]), store, extractor


def send(bot, text, mid, sender="alice", attachments=None):
    return bot.handle(
        Incoming(
            account_id="bot1",
            sender_id=sender,
            message_id=str(mid),
            text=text,
            attachments=attachments or [],
        )
    )


def full_case(bot, sender="alice"):
    send(bot, "新建 A001", "new", sender)
    send(bot, "订单 测试订单", "order", sender)
    return send(bot, "出货单 测试出货单", "delivery", sender)


def test_happy_path_and_revision_bound_human_confirmation(setup):
    bot, store, _ = setup
    reply = full_case(bot)
    assert "比对无差异" in reply
    peer = scope("bot1", "alice")
    assert store.case(peer, "A001")["approved_revision"] is None
    assert "已记录" in send(bot, "确认 A001 v2", "confirm")
    assert store.case(peer, "A001")["approved_revision"] == 2
    send(bot, "出货单", "replace-intent")
    assert store.case(peer, "A001")["approved_revision"] is None
    assert "未记录" in send(bot, "确认 A001 v2", "old-confirm")
    assert "等待" in send(bot, "审核", "pending-review")
    send(bot, "新的出货单文字", "new-delivery")
    assert "未记录" in send(bot, "确认 A001 v2", "stale-confirm")
    assert store.case(peer, "A001")["revision"] == 3


def test_messages_are_idempotent_and_peers_isolated(setup):
    bot, store, extractor = setup
    full_case(bot)
    assert extractor.calls == 2
    send(bot, "出货单 测试出货单", "delivery")
    assert extractor.calls == 2
    assert store.case(scope("bot1", "alice"), "A001")["revision"] == 2
    assert "先发送" in send(bot, "审核", "bob-review", "bob")
    assert "先发送" in send(bot, "确认 A001 v2", "bob-confirm", "bob")


def test_differences_cannot_be_confirmed_by_a_chat_instruction(setup):
    bot, store, extractor = setup
    send(bot, "新建 A001", "new")
    send(bot, "订单 测试", "order")
    extractor.document = doc(item={"quantity": "12", "amount": "1020"}, total="1020")
    assert "数量不同" in send(bot, "出货单 测试", "delivery")
    assert "未记录" in send(bot, "确认 A001 v2", "confirm")
    assert store.case(scope("bot1", "alice"), "A001")["approved_revision"] is None


def test_file_path_and_type_guard(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    path = media / "receipt.png"
    Image.new("RGB", (20, 20), "white").save(path)
    assert validate_image(path, [media]) == path
    outside = tmp_path / "private.png"
    Image.new("RGB", (20, 20), "white").save(outside)
    with pytest.raises(ExtractionError):
        validate_image(outside, [media])
    bad = media / "fake.png"
    bad.write_text("not an image")
    with pytest.raises(ExtractionError):
        validate_image(bad, [media])


def test_unlabelled_and_multiple_images_are_not_guessed(setup, tmp_path):
    bot, _, extractor = setup
    send(bot, "新建 A001", "new")
    assert "先发送" in send(bot, "[图片]", "unlabelled", attachments=[{"path": "not-read.png"}])
    send(bot, "订单", "label")
    reply = send(bot, "[图片]", "many", attachments=[{"path": "one.png"}, {"path": "two.png"}])
    assert "限一张" in reply
    assert extractor.calls == 0


@pytest.mark.asyncio
async def test_bridge_requires_token_and_is_scoped(setup):
    bot, _, _ = setup
    body = {"account_id": "b", "sender_id": "s", "message_id": "1", "text": "帮助"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(bot, "t" * 48)), base_url="http://test"
    ) as client:
        assert (await client.post("/message", json=body)).status_code == 401
        assert (
            await client.post("/message", json=body, headers={"Authorization": "Bearer wrong"})
        ).status_code == 401
        response = await client.post(
            "/message", json=body, headers={"Authorization": "Bearer " + "t" * 48}
        )
        assert response.status_code == 200
        assert "嘉惠" in response.json()["reply"]
        assert (await client.get("/health")).json()["service"] == "jiahui-shipment"


def test_extractor_uses_real_api_contract_and_keeps_missing_values():
    extracted = doc(item={"unit_price": None}).model_dump(mode="json")
    seen = []

    def transport(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": __import__("json").dumps(extracted, ensure_ascii=False)
                        },
                    }
                ]
            },
        )

    extractor = Extractor("private-key", transport=httpx.MockTransport(transport))
    result = extractor.parse("蛋液 10kg 10箱 单价85 金额850")
    assert result.lines[0].unit_price is None
    assert seen[0].headers["authorization"] == "Bearer private-key"
    assert seen[0].url.host == "api.deepseek.com"


def test_fabricated_evidence_blocks_a_clean_report():
    extracted = doc().model_dump(mode="json")
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": __import__("json").dumps(extracted)},
                    }
                ]
            },
        )
    )
    result = Extractor("key", transport=transport).parse("这里没有商品数据")
    assert result.uncertainties


@pytest.mark.parametrize("status", [401, 402, 429, 500])
def test_provider_errors_do_not_leak_secret(status):
    transport = httpx.MockTransport(
        lambda r: httpx.Response(status, text="server diagnostic secret-key")
    )
    with pytest.raises(ExtractionError) as exc:
        Extractor("secret-key", transport=transport).parse("单据")
    assert "secret-key" not in str(exc.value)


def test_truncated_model_output_is_not_saved():
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200, json={"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}
        )
    )
    with pytest.raises(ExtractionError, match="截断"):
        Extractor("key", transport=transport).parse("单据")
