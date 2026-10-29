"""Manual local-file review. Never imports or connects to WeChat."""

import json
import os
from pathlib import Path

from shipment_bot.auto_review import summarize
from shipment_bot.extract import ExtractionError, Extractor, validate_image


def validate_request(request):
    if not isinstance(request, dict):
        raise ValueError("审核请求格式无效，请重新选择材料。")
    paths = request.get("images", [])
    text = request.get("text", "")
    if not isinstance(paths, list) or len(paths) > 4 or any(not isinstance(p, str) for p in paths):
        raise ValueError("每次最多选择4张图片。")
    if not isinstance(text, str) or len(text) > 20000:
        raise ValueError("核对文字最多20000字。")
    if not paths and not text.strip():
        raise ValueError("请先选择/粘贴图片，或填写待审核文字。")
    if request.get("compare") and text.strip() and len(paths) > 1:
        raise ValueError("图文比对请先选择1张图片；多图自查可取消图文比对。")
    return paths, text.strip()


def review_request(request, config, key, *, factory=Extractor, progress=lambda text: None):
    paths, text = validate_request(request)
    llm, audit = config.get("llm", {}), config.get("audit", {})
    extractor = factory(
        key,
        base_url=llm.get("base_url", "https://api.deepseek.com"),
        model=llm.get("model", "deepseek-chat"),
        instructions=audit.get("instructions", ""),
        skill=audit.get("skill", ""),
    )
    items, errors = [], []
    compared_text = False
    for index, value in enumerate(paths, 1):
        try:
            progress(f"正在读取第{index}张本机图片并执行OCR…")
            path = Path(value).resolve(strict=True)
            # These are the exact files explicitly selected by the user.
            path = validate_image(path, [path.parent])
            item = extractor.auto_image(path)
            if len(item.document.lines) > 30:
                raise ExtractionError("每张最多30行商品，请拆分后审核。")
            item.image_index = index
            items.append(item)
        except (ExtractionError, OSError) as error:
            reason = (
                str(error) if isinstance(error, ExtractionError) else "图片文件不存在或不可读取。"
            )
            errors.append(f"第{index}张图片未完成识别：{reason}")
    if text:
        try:
            progress("正在提取本次核对文字…")
            interpreter = getattr(extractor, "interpret_request", None)
            body = text
            if callable(interpreter):
                intent = interpreter(text, context={"image_count": len(paths), "text_count": 0})
                if intent.action in {"ignore", "clarify"}:
                    raise ExtractionError("请提供需要审核的单据文字，或清空文字框只审核图片。")
                body = intent.document_text
                if not body and (intent.action == "compare" or not paths):
                    raise ExtractionError("尚未提供可核对的文字正文，请粘贴实际订单内容。")
            if body:
                item = extractor.auto_text(body)
                if len(item.document.lines) > 30:
                    raise ExtractionError("文字最多30行商品，请拆分后审核。")
                item.image_index = len(paths) + 1
                items.append(item)
                compared_text = True
        except ExtractionError as error:
            errors.append(f"文字未完成识别：{error}")
    return summarize(
        items,
        errors,
        bool(audit.get("require_customer")),
        bool(audit.get("require_specification")),
        compare_requested=bool(
            request.get("compare") and paths and (compared_text or errors and text)
        ),
    )


def main():
    directory = Path(os.environ["JIAHUI_AUDIT_DATA_DIR"])
    status = Path(os.environ["JIAHUI_AUDIT_STATUS"])
    request = json.loads(Path(os.environ["JIAHUI_LOCAL_REQUEST"]).read_text(encoding="utf-8"))
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key and (directory / "api-key.txt").exists():
        key = (directory / "api-key.txt").read_text(encoding="utf-8").strip()

    def publish(state, **fields):
        temporary = status.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"state": state, **fields}, ensure_ascii=False), encoding="utf-8"
        )
        temporary.replace(status)

    try:
        report = review_request(
            request, config, key, progress=lambda text: publish("running", progress=text)
        )
        publish("reviewed", report=report)
        print("窗口审核已结束，结果仅显示在软件窗口，未回群。", flush=True)
    except Exception as error:
        # Do not expose third-party exception messages or any credentials.
        message = (
            str(error)
            if isinstance(error, ExtractionError | ValueError)
            else f"窗口审核未完成（{type(error).__name__}），请检查图片和API设置后重试。"
        )
        if key:
            message = message.replace(key, "[Key 已隐藏]")
        publish("error", error=message)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
