"""Real loopback HTTP + Chromium smoke using temporary, synthetic Jiahui data.

Run with a Python that has Playwright installed, e.g.:
    python scripts/smoke_jiahui_browser.py
The application uses the repository .venv. No credentials or session cookies are
written to reports. The temporary account/data/server are removed on completion.
This intentionally validates the explicit offline provider path, not a real LLM.
"""

from __future__ import annotations

import argparse
import csv
import http.cookiejar
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def http_json(opener, base, path, payload=None):
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = urllib.request.Request(
        base + path,
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with opener.open(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def run(port: int, app_python: Path, chromium: str) -> dict:
    from playwright.sync_api import sync_playwright

    result = {
        "started_at": datetime.now(UTC).isoformat(),
        "target": f"127.0.0.1:{port}",
        "data": "temporary synthetic fixtures; no formal Jiahui data modified",
        "providers": "explicitly isolated offline path; not a real model verification",
        "checks": {},
    }
    checks = result["checks"]
    base = f"http://127.0.0.1:{port}"
    # Do not take over an existing service, including the user's local deployment.
    probe = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        probe.open(base + "/api/v1/health", timeout=1).close()
    except urllib.error.URLError:
        pass
    else:
        raise RuntimeError(f"Port {port} already serves HTTP; choose an unused --port")

    with tempfile.TemporaryDirectory(prefix="jiahui-browser-smoke-") as temporary:
        workspace = Path(temporary)
        data_dir = workspace / "data"
        demo = ROOT / "docs/jiahui/demo-only/演示格式_非真实产品.csv"
        with demo.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames
            rows = list(reader)
        for row in rows:
            row["状态"] = "current"
        hidden = dict(rows[0])
        hidden.update(
            {
                "内部记录ID": "DEMO-HIDDEN-ONLY",
                "商品编号": "00002",
                "名称": "【隔离测试】受限采购原料",
                "状态": "current",
                "访问范围": "procurement-only",
            }
        )
        rows.append(hidden)
        catalog = workspace / "isolated-products.csv"
        with catalog.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        knowledge = workspace / "isolated-knowledge.txt"
        knowledge.write_text(
            "【隔离测试资料，非真实业务制度】隔离收货核对流程：收货时核对标签与批次。",
            encoding="utf-8",
        )
        username = "smoke_operator"
        password = secrets.token_urlsafe(32)
        limited_username = "smoke_knowledge_only"
        limited_password = secrets.token_urlsafe(32)
        setup = """
import json,sys
from app.jiahui.auth import AuthStore
from app.jiahui.import_catalog import import_csv
from app.jiahui.import_knowledge import import_knowledge
x=json.load(sys.stdin)
s=AuthStore(x['data'])
s.create_user(x['username'],x['password'],'sales',frozenset({'products','knowledge'}))
s.create_user(x['limited_username'],x['limited_password'],'finance',frozenset({'knowledge'}))
first=import_csv(x['catalog'],x['data'],status='current')
second=import_csv(x['catalog'],x['data'],status='current')
assert second['changed'] is False
import_knowledge(x['knowledge'],x['data'],document_id='SMOKE-KNOWLEDGE-ONLY',version='TEST-ONLY',location='PDF第99页 第1节（隔离测试位置）',status='current',source_file='隔离测试资料_非真实业务.pdf')
"""
        subprocess.run(
            [str(app_python), "-c", setup],
            cwd=ROOT,
            input=json.dumps(
                {
                    "data": str(data_dir),
                    "catalog": str(catalog),
                    "knowledge": str(knowledge),
                    "username": username,
                    "password": password,
                    "limited_username": limited_username,
                    "limited_password": limited_password,
                }
            ),
            text=True,
            check=True,
            capture_output=True,
        )
        checks["repeatable_import"] = "passed; synthetic CSV imported twice with no second change"
        env = os.environ.copy()
        env.update(
            {
                "BUSINESS_PROFILE": "jiahui",
                "JIAHUI_DATA_DIR": str(data_dir),
                "APP_ENV": "testing",
                "LLM_BACKEND": "auto",
                "DEEPSEEK_API_KEY": "",
                "EMBEDDING_API_KEY": "",
                "EMBEDDING_BASE_URL": "",
                "LOCAL_EMBEDDING_FORCE": "false",
                "CROSS_ENCODER_FORCE": "false",
                "LOCAL_EMBEDDING_ENABLED": "false",
                "CROSS_ENCODER_ENABLED": "false",
            }
        )
        server_log = workspace / "server.log"
        with server_log.open("wb") as log:
            process = subprocess.Popen(
                [
                    str(app_python),
                    "-m",
                    "uvicorn",
                    "app.asgi:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 45
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(
                            "Isolated application exited before readiness; inspect local setup dependencies"
                        )
                    try:
                        code, _ = http_json(probe, base, "/api/v1/health")
                        if code == 200:
                            break
                    except urllib.error.URLError:
                        pass
                    if time.monotonic() > deadline:
                        raise TimeoutError("Isolated application readiness exceeded 45 seconds")
                    time.sleep(0.25)
                status, _ = http_json(
                    probe,
                    base,
                    "/api/v1/chat",
                    {"message": "查询编号00001", "user_id": "spoofed-client-id"},
                )
                assert status == 401, "Unauthenticated question must return 401"
                checks["unauthenticated_http"] = "passed; HTTP 401"
                auth = urllib.request.build_opener(
                    urllib.request.ProxyHandler({}),
                    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                )
                code, principal = http_json(
                    auth, base, "/api/v1/auth/login", {"username": username, "password": password}
                )
                assert code == 200 and principal["username"] == username
                code, profile = http_json(auth, base, "/api/v1/profile")
                assert (
                    code == 200 and profile["product_count"] == 2
                ), "Restricted records must not leak via profile counts"
                assert profile["capabilities"]["llm"] == "offline_excerpt"
                checks["authenticated_profile"] = (
                    "passed; two visible products, restricted record excluded; offline clearly declared"
                )

                def ask(message):
                    status, response = http_json(
                        auth,
                        base,
                        "/api/v1/chat",
                        {"message": message, "user_id": "spoofed-client-id"},
                    )
                    assert status == 200, f"Chat returned HTTP {status}"
                    return response

                clarification = ask("查询编号00001")
                assert "请先选择" in clarification["answer"]
                assert (
                    "演示规格甲" in clarification["answer"]
                    and "演示规格乙" in clarification["answer"]
                )
                assert "00001" in clarification["answer"] and len(clarification["citations"]) == 2
                checks["leading_zero_duplicate_clarification"] = (
                    "passed; exact 00001 returns two independent choices and citations"
                )
                selected = ask("查询内部记录ID DEMO-ONLY-001")
                assert "演示规格甲" in selected["answer"] and "演示规格乙" not in selected["answer"]
                assert (
                    len(selected["citations"]) == 1
                    and "第2行" in selected["citations"][0]["section"]
                )
                checks["selection_and_row_citation"] = (
                    "passed; independent record ID selects one specification with CSV row location"
                )
                comparison = ask("对比编号00001的两种规格")
                assert all(word in comparison["answer"] for word in ["演示规格甲", "演示规格乙"])
                checks["comparison"] = "passed; both imported specifications remain sourced"
                missing = ask("查询编号999999")
                assert "尚未录入" in missing["answer"] or "需要确认" in missing["answer"]
                assert not missing["citations"]
                checks["missing_data"] = (
                    "passed; explicit missing/confirmation answer without invented source"
                )
                hidden = ask("查询编号00002")
                assert "受限采购原料" not in hidden["answer"] and not hidden["citations"]
                checks["retrieval_permission"] = (
                    "passed; restricted record absent from answer and citations"
                )
                knowledge_answer = ask("隔离收货核对流程")
                assert (
                    "非生成式回答" in knowledge_answer["answer"]
                    and "核对标签与批次" in knowledge_answer["answer"]
                )
                assert any("PDF第99页 第1节" in c["section"] for c in knowledge_answer["citations"])
                checks["knowledge_page_citation"] = (
                    "passed; real retrieval with explicit offline excerpt and retained page/section"
                )
                erp = ask("查询这个产品的库存")
                assert "尚未接入业务数据" in erp["answer"]
                checks["unconnected_erp"] = "passed; no invented inventory"
                write = ask("取消订单TEST-ONLY")
                assert write["refused"] and not write["pending_action"]
                checks["business_writes_disabled"] = "passed; refused without action proposal"
                other = urllib.request.build_opener(
                    urllib.request.ProxyHandler({}),
                    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                )
                code, _ = http_json(
                    other,
                    base,
                    "/api/v1/auth/login",
                    {"username": limited_username, "password": limited_password},
                )
                assert code == 200
                code, denied = http_json(
                    other, base, "/api/v1/chat", {"message": "查询编号00001", "user_id": username}
                )
                assert (
                    code == 200 and "演示规格甲" not in denied["answer"] and not denied["citations"]
                )
                checks["server_owned_identity"] = (
                    "passed; knowledge-only account cannot impersonate products account via user_id"
                )

                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=chromium, headless=True)
                    try:
                        context = browser.new_context(viewport={"width": 1280, "height": 900})
                        page = context.new_page()
                        page.goto(base)
                        page.get_by_role("button", name="登录", exact=True).wait_for()
                        page.get_by_label("账号", exact=True).fill(username)
                        page.get_by_label("密码", exact=True).fill(password)
                        page.get_by_role("button", name="登录", exact=True).click()
                        page.get_by_role("button", name="退出登录", exact=True).wait_for()
                        assert "非生成式回答" in page.locator("body").inner_text()
                        page.get_by_role("textbox", name="输入消息").fill("查询编号00001")
                        page.get_by_role("button", name="发送消息").click()
                        page.get_by_text("请先选择", exact=False).wait_for()
                        page.get_by_role("textbox", name="输入消息").fill(
                            "查询内部记录ID DEMO-ONLY-001"
                        )
                        page.get_by_role("button", name="发送消息").click()
                        page.get_by_text("以下为已录入资料的结构化查询结果", exact=False).wait_for()
                        assert (
                            "导入CSV isolated-products.csv 第2行"
                            in page.locator("body").inner_text()
                        )
                        assert page.evaluate("localStorage.getItem('adaptive-rag-web:v1')") is None
                        checks["browser_real_login_chat_citation"] = (
                            "passed; real cookie login, SSE clarification and selected product with row source"
                        )
                        page.set_viewport_size({"width": 390, "height": 844})
                        assert not page.evaluate(
                            "document.documentElement.scrollWidth > innerWidth"
                        )
                        checks["mobile_workspace"] = (
                            "passed; 390px workspace has no horizontal page overflow"
                        )
                        page.get_by_role("button", name="退出登录", exact=True).click()
                        page.get_by_role("button", name="登录", exact=True).wait_for()
                        assert "演示规格甲" not in page.locator("body").inner_text()
                        status = context.request.post(
                            base + "/api/v1/chat",
                            data={"message": "查询编号00001", "user_id": username},
                        ).status
                        assert status == 401
                        checks["browser_logout"] = (
                            "passed; conversation cleared and cookie no longer authorizes HTTP"
                        )
                    finally:
                        browser.close()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    result["status"] = "passed"
    result["completed_at"] = datetime.now(UTC).isoformat()
    result["cleanup"] = "temporary data/account removed; isolated server stopped"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument(
        "--python",
        type=Path,
        default=ROOT / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"),
    )
    parser.add_argument(
        "--chromium", default=shutil.which("chromium") or shutil.which("chromium-browser")
    )
    args = parser.parse_args()
    if not args.chromium:
        parser.error("Pass --chromium with an installed Chromium executable")
    report = ROOT / "logs/jiahui-browser-smoke.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = run(args.port, args.python, args.chromium)
    except Exception as error:
        # Exception descriptions and request bodies can contain data; keep the
        # report free of raw credentials/cookies and preserve a failed exit code.
        result = {
            "status": "failed",
            "error_type": type(error).__name__,
            "completed_at": datetime.now(UTC).isoformat(),
        }
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Jiahui browser/HTTP smoke passed ({len(result['checks'])} checks); report: {report}")


if __name__ == "__main__":
    main()
