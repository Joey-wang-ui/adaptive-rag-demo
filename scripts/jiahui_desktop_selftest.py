"""Packaged Windows real-HTTP/native-window checks with ephemeral accounts only."""

from __future__ import annotations

import csv
import http.cookiejar
import json
import os
import secrets
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def self_test():
    from app.jiahui.auth import AuthStore
    from app.jiahui.vault import WindowsVault
    from scripts.jiahui_desktop import DesktopRuntime, ensure_webview_runtime

    result = {
        "checks": {},
        "fixtures": "temporary synthetic data; no real credentials or business data",
    }
    checks = result["checks"]
    report = Path(os.environ.get("JIAHUI_SELF_TEST_REPORT", "desktop-self-test.json"))
    original = {
        key: os.environ.get(key)
        for key in ["APP_ENV", "DEEPSEEK_API_KEY", "EMBEDDING_API_KEY", "LLM_BACKEND"]
    }
    os.environ.update(
        {
            "APP_ENV": "testing",
            "DEEPSEEK_API_KEY": "",
            "EMBEDDING_API_KEY": "",
            "LLM_BACKEND": "auto",
        }
    )
    try:
        with tempfile.TemporaryDirectory(prefix="jiahui-packaged-") as temporary:
            home = Path(temporary)
            store = AuthStore(home / "data")
            password = secrets.token_urlsafe(24)
            assert store.create_user("package-test", password, "admin")
            source = home / "products.csv"
            with source.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "内部记录ID",
                        "商品编号",
                        "名称",
                        "规格",
                        "来源文件",
                        "来源位置",
                        "版本",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "内部记录ID": "DEMO-ONLY-001",
                        "商品编号": "00001",
                        "名称": "【打包隔离测试】原料",
                        "规格": "25kg/包",
                        "来源文件": "隔离测试.pdf",
                        "来源位置": "PDF第4页",
                        "版本": "DEMO",
                    }
                )
            from app.jiahui.import_catalog import import_csv

            import_csv(source, home / "data", status="current")
            runtime = DesktopRuntime(home)
            try:
                runtime.start(timeout=90)
                base = runtime.url
                jar = http.cookiejar.CookieJar()
                client = urllib.request.build_opener(
                    urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar)
                )

                def request(path, payload=None):
                    body = (
                        json.dumps(payload, ensure_ascii=False).encode()
                        if payload is not None
                        else None
                    )
                    req = urllib.request.Request(
                        base + path,
                        data=body,
                        headers={"Content-Type": "application/json"} if body else {},
                    )
                    with client.open(req, timeout=30) as response:
                        return response.status, response.read()

                status, body = request("/")
                assert status == 200 and b'id="root"' in body
                checks["bundled_frontend"] = True
                try:
                    request("/api/v1/chat", {"message": "编号00001"})
                    raise AssertionError("unauthenticated chat accepted")
                except urllib.error.HTTPError as exc:
                    assert exc.code == 401
                checks["authentication_gate"] = True
                status, _ = request(
                    "/api/v1/auth/login", {"username": "package-test", "password": password}
                )
                assert status == 200 and jar
                checks["real_cookie_login"] = True
                _, body = request("/api/v1/chat", {"message": "编号00001", "user_id": "forged"})
                answer = json.loads(body)
                assert "25kg/包" in answer["answer"] and answer["citations"]
                checks["hybrid_graph_answer_citation"] = True
                _, body = request("/api/v1/profile")
                profile = json.loads(body)
                assert profile["capabilities"]["llm"] == "offline_excerpt"
                assert profile["capabilities"]["embedding"] == "hash"
                checks["honest_model_fallback"] = True
                _, body = request("/api/v1/business/prices?identifier=00001")
                assert json.loads(body)["message"] == "尚未接入业务数据"
                checks["unconnected_erp"] = True
                import base64

                from scripts.jiahui_pdf_fixture import synthetic_catalog_pdf

                pdf_upload = {
                    "filename": "isolated-catalog.pdf",
                    "content": base64.b64encode(synthetic_catalog_pdf()).decode(),
                    "version": "TEST-reference",
                }
                _, payload = request("/api/v1/desktop/import/pdf", pdf_upload)
                imported = json.loads(payload)
                assert imported["catalog_imported"] == 2 and imported["imported_pages"] == 1
                _, payload = request("/api/v1/chat", {"message": "编号00009"})
                assert "请先选择" in json.loads(payload)["answer"]
                _, payload = request("/api/v1/chat", {"message": "没资料吗"})
                assert "有资料" in json.loads(payload)["answer"]
                checks["real_pdf_import_and_catalogue_query"] = True
                runtime.stop()
                runtime = DesktopRuntime(home)
                runtime.start(timeout=90)
                base = runtime.url
                request("/api/v1/auth/login", {"username": "package-test", "password": password})
                _, payload = request("/api/v1/desktop/import/pdf", pdf_upload)
                assert not json.loads(payload)["catalog_changed"]
                _, payload = request("/api/v1/profile")
                assert json.loads(payload)["product_count"] == 3
                checks["restart_and_repeat_import_reuse_without_duplicates"] = True
                from app.jiahui.auth import AuthStore as LocalStore

                local_store = LocalStore(home / "data")
                # Recreating the runtime must retain the original password hash.
                assert (
                    local_store.principal(
                        local_store.login("package-test", password, "upgrade-check")
                    ).username
                    == "package-test"
                )
                checks["upgrade_keeps_original_account_password"] = True
                if os.name == "nt":
                    vault = WindowsVault(home)
                    secret = secrets.token_urlsafe(32)
                    try:
                        vault.write(secret)
                        assert vault.read() == secret
                        checks["windows_credential_roundtrip"] = True
                    finally:
                        vault.delete()
                    # Exercise the real packaged Tk recovery form with test data only.
                    from unittest.mock import patch

                    from app.jiahui.recovery import run_recovery

                    runtime.stop()
                    new_password = secrets.token_urlsafe(24)
                    recovery_errors = []

                    def automate_recovery(root, selected, field, confirmation, save):
                        try:
                            selected.set("package-test")
                            field.set(new_password)
                            confirmation.set(new_password)
                            save()
                        except Exception as exc:
                            recovery_errors.append(type(exc).__name__)
                            root.destroy()

                    with (
                        patch("tkinter.messagebox.showinfo"),
                        patch(
                            "tkinter.messagebox.showerror",
                            side_effect=RuntimeError("recovery-form-error"),
                        ),
                    ):
                        recovered = run_recovery(home / "data", on_ready=automate_recovery)
                    assert recovered and not recovery_errors
                    runtime = DesktopRuntime(home)
                    runtime.start(timeout=90)
                    base = runtime.url
                    request(
                        "/api/v1/auth/login", {"username": "package-test", "password": new_password}
                    )
                    _, payload = request("/api/v1/profile")
                    assert json.loads(payload)["product_count"] == 3
                    checks["native_recovery_form_preserves_catalogue_and_account"] = True
                    ensure_webview_runtime()
                    import webview

                    window = webview.create_window("嘉惠软件包自检", base, width=1100, height=760)
                    errors = []

                    def inspect():
                        try:
                            deadline = time.monotonic() + 40
                            while time.monotonic() < deadline:
                                heading = window.evaluate_js(
                                    'document.querySelector("h1")?.textContent || ""'
                                )
                                if heading and "嘉惠" in heading:
                                    checks["native_edge_window_and_react"] = True
                                    break
                                time.sleep(0.3)
                            else:
                                raise AssertionError("React did not render in native window")
                        except Exception as exc:
                            errors.append(type(exc).__name__)
                        finally:
                            window.destroy()

                    webview.start(inspect, gui="edgechromium", debug=False, private_mode=True)
                    assert not errors, ",".join(errors)
                request("/api/v1/auth/logout", {})
                checks["logout"] = True
            finally:
                runtime.stop()
            checks["owned_service_stopped"] = runtime.process.poll() is not None
        result["passed"] = all(checks.values())
    except Exception as exc:
        result["passed"] = False
        result["error_type"] = type(exc).__name__
        raise
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Desktop packaged self-test passed.")
