import argparse
import http.cookiejar
import json
import os
import secrets
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.jiahui.auth import AuthStore  # noqa: E402
from app.jiahui.pdf_import import import_pdf  # noqa: E402
from scripts.jiahui_desktop import DesktopRuntime  # noqa: E402

os.environ.update(APP_ENV="testing", DEEPSEEK_API_KEY="", EMBEDDING_API_KEY="", LLM_BACKEND="auto")
parser = argparse.ArgumentParser(
    description="嘉惠2026产品目录的隔离离线HTTP问答验收，不测真实模型幻觉率"
)
parser.add_argument("--pdf", type=Path, required=True)
parser.add_argument("--report", type=Path, default=ROOT / "logs/common-question-results.json")
args = parser.parse_args()
source = args.pdf
results = []
with tempfile.TemporaryDirectory(prefix="jiahui-common-") as t:
    home = Path(t)
    pw = secrets.token_urlsafe(24)
    AuthStore(home / "data").create_user("test", pw, "admin")
    import_pdf(source.read_bytes(), home / "data", filename="private.pdf", version="2026.4")
    runtime = DesktopRuntime(home)
    try:
        runtime.start()
        client = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )

        def req(path, payload):
            r = urllib.request.Request(
                runtime.url + "/api/v1/" + path,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with client.open(r, timeout=60) as response:
                return json.load(response)

        req("auth/login", {"username": "test", "password": pw})
        cases = []
        for q in [
            "帮我查下蛋液有哪些",
            "蛋液有什么",
            "帮我看看哪些蛋液",
            "帮我查下奶油有哪些",
            "面粉有哪些",
            "帮我查下安佳有哪些产品",
        ]:
            cases.append((q, lambda a: "| 产品名称 |" in a["answer"] and bool(a["citations"])))
        for q in [
            "模型连接了吗",
            "查一下模型链接",
            "DeepSeek连上了吗",
            "大模型现在正常吗",
            "你是什么模型",
            "模型接好了吗",
            "现在用什么模型",
        ]:
            cases.append(
                (q, lambda a: "没有连接可用的大模型" in a["answer"] and not a["citations"])
            )
        for q in ["编号06401的规格是什么", "查一下06401", "帮我查下06401的资料"]:
            cases.append((q, lambda a: "06401" in a["answer"] and bool(a["citations"])))
        for q in ["编号1564", "1564的规格是什么"]:
            cases.append((q, lambda a: "请先选择" in a["answer"]))
        cases.append(
            (
                "对比1564的两个规格",
                lambda a: "| 产品名称 |" in a["answer"] and len(a["citations"]) == 2,
            )
        )
        for q in ["没资料吗", "有哪些资料", "导入成功了吗"]:
            cases.append((q, lambda a: "324" in a["answer"]))
        for q in ["蛋液现在多少钱", "奶油库存多少", "查一下订单", "有现货吗", "查询配送状态"]:
            cases.append((q, lambda a: "尚未接入业务数据" in a["answer"]))
        for q in ["取消订单", "退款", "扣减库存"]:
            cases.append((q, lambda a: "不开放业务写操作" in a["answer"]))
        cases.append(
            ("编号99999999的资料", lambda a: not a["citations"] and "未找到依据" in a["answer"])
        )
        for q, check in cases:
            a = req("chat", {"message": q})
            results.append({"q": q, "pass": bool(check(a)), "answer": a["answer"]})
        a = req("chat", {"message": "编号06401的资料"})
        session = a["session_id"]
        for q in ["那保质期呢", "这个怎么储存"]:
            a = req("chat", {"message": q, "session_id": session})
            results.append(
                {
                    "q": "连续追问：" + q,
                    "pass": bool(a["citations"]) and "06401" in a["answer"],
                    "answer": a["answer"],
                }
            )
    finally:
        runtime.stop()
args.report.parent.mkdir(parents=True, exist_ok=True)
args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(
    json.dumps(
        {
            "total": len(results),
            "passed": sum(r["pass"] for r in results),
            "failed": [
                {"q": r["q"], "answer": r["answer"][:120]} for r in results if not r["pass"]
            ],
        },
        ensure_ascii=False,
    )
)

if not all(r["pass"] for r in results):
    raise SystemExit(1)
