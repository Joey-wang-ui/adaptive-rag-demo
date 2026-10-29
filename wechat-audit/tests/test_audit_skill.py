import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from shipment_bot.audit_skill import ReviewIntent, builtin_skill, literal_numbers
from shipment_bot.auto_review import Recognized, summarize
from shipment_bot.desktop_state import save_settings
from shipment_bot.extract import ExtractionError, Extractor
from shipment_bot.group_audit import GroupAudit
from shipment_bot.models import Document, Line


def document():
    return Document(
        customer="朴恩",
        total="100",
        lines=[Line(name="蛋液", quantity="2", unit="箱", unit_price="50", price_unit="箱")],
    )


def test_bundled_skill_covers_workflow_and_override_is_used_in_real_api_request():
    assert all(word in builtin_skill() for word in ["接单", "图片", "计算", "比对", "去重", "急停"])

    def transport(request):
        body = json.loads(request.content)
        assert "自定义：特别检查蛋液数量" in body["messages"][0]["content"]
        assert "这个跟图里对得上吗" in body["messages"][1]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps({"action": "compare", "scope": "recent"})
                        },
                    }
                ]
            },
        )

    extractor = Extractor(
        "test-only", skill="自定义：特别检查蛋液数量", transport=httpx.MockTransport(transport)
    )
    assert extractor.interpret_request("这个跟图里对得上吗").action == "compare"


def test_router_cannot_invent_rewrite_material_or_request_os_actions():
    def response(value):
        return httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps(value)}}]}
            )
        )

    extractor = Extractor(
        "test-only",
        transport=response({"action": "review", "scope": "inline", "document_text": "捏造的订单"}),
    )
    with pytest.raises(ExtractionError, match="改写"):
        extractor.interpret_request("帮我看看 蛋液2箱")
    extractor.transport = response({"action": "ship", "scope": "recent"})
    with pytest.raises(ExtractionError):
        extractor.interpret_request("直接发货")


def test_natural_request_compares_own_batch_without_fixed_titles_or_ids(tmp_path):
    caption, image = document(), document()
    image.lines[0].quantity = Decimal("3")
    extract = SimpleNamespace(
        interpret_request=Mock(
            return_value=ReviewIntent(
                action="compare", scope="inline", document_text="朴恩要2箱蛋液，每箱50元，合计100元"
            )
        ),
        auto_text=Mock(return_value=Recognized(document=caption, source_type="text")),
        auto_image=Mock(return_value=Recognized(document=image, source_type="image")),
    )
    bot = GroupAudit(
        tmp_path / "state.db",
        "g@chatroom",
        "self",
        "审单bot",
        extract,
        Mock(return_value=tmp_path / "image.jpg"),
    )

    def message(n, text="", kind="文本", sender="member"):
        return dict(
            local_id=n, sort_seq=n, shard="m.db", sender_username=sender, type=kind, content=text
        )

    bot.handle(bot.group, message(1, kind="图片"))
    result = bot.handle(
        bot.group, message(2, "@审单bot 朴恩要2箱蛋液，每箱50元，合计100元，跟图里对得上吗")
    )
    assert "数量不一致" in result and "文字为2，图片为3" in result
    assert "单号" not in result[result.index("【按本次请求核对图文】") :]
    assert bot.handle(bot.group, message(3, "@审单bot 再帮我看看")) is None
    assert extract.interpret_request.call_count == 1
    assert bot.handle("private", message(4, "@审单bot 帮我看看")) is None
    assert extract.interpret_request.call_count == 1


def test_explicit_comparison_stays_incomplete_when_image_failed():
    result = summarize(
        [Recognized(document=document(), source_type="text")],
        ["图片解密失败"],
        compare_requested=True,
    )
    assert "图文比对未完成" in result
    assert "字段与本张图片一致" not in result


def test_same_named_multiple_rows_are_not_matched_by_guessing():
    caption, image = document(), document()
    image.lines.append(image.lines[0].model_copy())
    result = summarize(
        [Recognized(document=caption, source_type="text"), Recognized(document=image)],
        compare_requested=True,
    )
    assert "未找到唯一对应行" in result
    assert "字段与本张图片一致" not in result


def test_skill_persists_without_resetting_api_or_self_test(tmp_path):
    config = save_settings(
        tmp_path,
        "群",
        "审单bot",
        "test-key",
        True,
        allow_self_test=True,
        llm={"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
        audit={"skill": "我的完整审单技能"},
    )
    assert config["audit"]["skill"] == "我的完整审单技能"
    config = save_settings(
        tmp_path,
        "群",
        "审单bot",
        "test-key",
        True,
        allow_self_test=True,
        audit={"instructions": "附加要求"},
    )
    assert config["audit"]["skill"] == "我的完整审单技能"
    assert config["allow_self_test"] and config["llm"]["model"] == "deepseek-chat"


@pytest.mark.parametrize(
    "text,value",
    [
        ("两箱", "2"),
        ("每箱五十元", "50"),
        ("一百零二元", "102"),
        ("二百五十元", "250"),
        ("十二箱", "12"),
    ],
)
def test_explicit_chinese_numbers_are_supported_as_evidence(text, value):
    assert Decimal(value) in literal_numbers(text)


def test_ambiguous_chinese_number_is_not_guessed():
    assert Decimal("120") not in literal_numbers("一百二元")
