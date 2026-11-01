"""Downloadable trial desktop. No WeChat or model calls occur merely on opening."""

import json
import multiprocessing
import os
import sys
import threading
import uuid
from decimal import Decimal
from pathlib import Path

from shipment_bot.models import Document, Line
from shipment_bot.workbench import AuditService

FIELDS = [
    ("name", "商品名称"),
    ("specification", "规格"),
    ("quantity", "数量"),
    ("unit", "数量单位"),
    ("unit_price", "成交单价"),
    ("price_unit", "计价单位"),
    ("amount", "行金额"),
    ("sku", "商品编码（可空）"),
]


def data_directory():
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return base / "JiahuiAuditTrial"


def self_test():
    import tempfile
    import tkinter as tk

    from shipment_bot.extract import Extractor

    Extractor("self-test-only").prepare_ocr()
    root = tk.Tk()
    root.withdraw()
    with tempfile.TemporaryDirectory() as folder:
        doc = Document(
            customer="演示客户",
            reference_order_id="DEMO",
            lines=[
                Line(
                    name="蛋液",
                    specification="10kg",
                    quantity=Decimal("10"),
                    unit="箱",
                    unit_price=Decimal("85"),
                    price_unit="箱",
                    amount=Decimal("850"),
                )
            ],
            total=Decimal("850"),
        )
        service = AuditService(Path(folder) / "test.sqlite3")
        service.confirm_order("DEMO", doc)
        assert service.audit("test", "DEMO", doc, "workbench")["status"] == "no_differences"
        app = Desktop(root, Path(folder))
        root.update_idletasks()
        assert app.service.orders() == []
    root.destroy()
    report = os.environ.get("JIAHUI_SELF_TEST_REPORT")
    if report:
        Path(report).write_text(
            json.dumps({"ocr": True, "tk": True, "engine": True}), encoding="utf-8"
        )
    return 0


class DocumentEditor:
    def __init__(self, parent):
        import tkinter as tk
        from tkinter import ttk

        self.frame = ttk.Frame(parent, padding=10)
        self.values = {key: tk.StringVar() for key in ("customer", "reference_order_id", "total")}
        for col, (key, label) in enumerate(
            (("customer", "客户"), ("reference_order_id", "订单号"), ("total", "合计"))
        ):
            ttk.Label(self.frame, text=label).grid(row=0, column=col * 2, sticky="w")
            ttk.Entry(self.frame, textvariable=self.values[key], width=20).grid(
                row=0, column=col * 2 + 1, padx=5
            )
        self.table = ttk.Treeview(
            self.frame, columns=[x[0] for x in FIELDS], show="headings", height=9
        )
        for key, label in FIELDS:
            self.table.heading(key, text=label)
            self.table.column(key, width=105, stretch=True)
        self.table.grid(row=1, column=0, columnspan=6, sticky="nsew", pady=10)
        self.table.bind("<Double-1>", lambda _event: self.edit())
        buttons = ttk.Frame(self.frame)
        buttons.grid(row=2, column=0, columnspan=6, sticky="w")
        ttk.Button(buttons, text="添加商品", command=lambda: self.edit(new=True)).pack(side="left")
        ttk.Button(buttons, text="修改选中行", command=self.edit).pack(side="left", padx=8)
        ttk.Button(
            buttons,
            text="删除选中行",
            command=lambda: [self.table.delete(i) for i in self.table.selection()],
        ).pack(side="left")
        self.notes = []
        self.frame.rowconfigure(1, weight=1)
        for c in range(6):
            self.frame.columnconfigure(c, weight=1)

    def edit(self, new=False):
        import tkinter as tk
        from tkinter import messagebox, ttk

        selected = self.table.selection()
        if not new and not selected:
            return
        target = None if new else selected[0]
        old = [""] * len(FIELDS) if new else self.table.item(target, "values")
        dialog = tk.Toplevel(self.frame)
        dialog.title("商品明细")
        entries = {}
        for i, (key, label) in enumerate(FIELDS):
            ttk.Label(dialog, text=label).grid(row=i, column=0, padx=12, pady=5, sticky="w")
            var = tk.StringVar(value=old[i])
            entries[key] = var
            ttk.Entry(dialog, textvariable=var, width=30).grid(row=i, column=1, padx=12, pady=5)

        def save():
            try:
                row = self.line([entries[k].get() for k, _ in FIELDS])
                vals = [getattr(row, k) if getattr(row, k) is not None else "" for k, _ in FIELDS]
                if target:
                    self.table.item(target, values=vals)
                else:
                    self.table.insert("", "end", values=vals)
                dialog.destroy()
            except Exception:
                messagebox.showerror(
                    "请检查商品", "数量、单价、金额须为非负数字；空白表示未识别。", parent=dialog
                )

        ttk.Button(dialog, text="保存本行", command=save).grid(row=len(FIELDS), column=1, pady=12)
        dialog.transient(self.frame.winfo_toplevel())
        dialog.grab_set()

    @staticmethod
    def line(values):
        raw = dict(zip((k for k, _ in FIELDS), values, strict=True))
        for key in ("quantity", "unit_price", "amount"):
            raw[key] = Decimal(raw[key]) if raw[key] != "" else None
        return Line.model_validate(raw)

    def document(self):
        return Document(
            customer=self.values["customer"].get().strip() or None,
            reference_order_id=self.values["reference_order_id"].get().strip() or None,
            total=Decimal(self.values["total"].get()) if self.values["total"].get() else None,
            lines=[self.line(self.table.item(i, "values")) for i in self.table.get_children()],
            uncertainties=self.notes,
        )

    def load(self, document):
        for key, var in self.values.items():
            value = getattr(document, key)
            var.set("" if value is None else str(value))
        self.table.delete(*self.table.get_children())
        for row in document.lines:
            self.table.insert(
                "",
                "end",
                values=[getattr(row, k) if getattr(row, k) is not None else "" for k, _ in FIELDS],
            )
        self.notes = list(document.uncertainties)


class Desktop:
    def __init__(self, root, directory):
        import tkinter as tk
        from tkinter import scrolledtext, ttk

        self.root, self.directory = root, directory
        directory.mkdir(parents=True, exist_ok=True)
        self.service = AuditService(directory / "jiahui.sqlite3")
        self.key = ""
        self.base_url, self.model = "https://api.deepseek.com", "deepseek-chat"
        self.worker = None
        self.busy = False
        root.title("嘉惠审核工作台 · 试用版 0.5")
        root.geometry("1100x850")
        self.status = tk.StringVar(value="已就绪 · 微信未连接 · 自动回群关闭")
        ttk.Label(
            root,
            text="嘉惠智能发货审核 · 独立工作台 / 微信只读试验",
            font=("Microsoft YaHei UI", 16),
        ).pack(pady=12)
        ttk.Label(root, textvariable=self.status).pack()
        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)
        order_page = ttk.Frame(notebook)
        audit_page = ttk.Frame(notebook)
        wechat_page = ttk.Frame(notebook, padding=16)
        notebook.add(order_page, text="1  确认订单")
        notebook.add(audit_page, text="2  出库单审核")
        notebook.add(wechat_page, text="3  微信只读试验")
        self.order = DocumentEditor(order_page)
        self.order.frame.pack(fill="both", expand=True)
        controls = ttk.Frame(order_page, padding=10)
        controls.pack(fill="x")
        ttk.Button(
            controls, text="选择订单照片并识别", command=lambda: self.extract(self.order)
        ).pack(side="left")
        ttk.Button(controls, text="保存为已确认订单", command=self.confirm).pack(
            side="left", padx=10
        )
        ttk.Button(controls, text="载入演示材料", command=self.demo).pack(side="left")
        ttk.Label(
            order_page,
            text="请先核对客户、订单号和每行商品，再确认保存；已确认订单不可覆盖，修改请用新版本订单号。",
            wraplength=1000,
        ).pack(padx=10, pady=10)
        selection = ttk.Frame(audit_page, padding=10)
        selection.pack(fill="x")
        ttk.Label(selection, text="选择已确认订单").pack(side="left")
        self.selected = tk.StringVar()
        self.orders_box = ttk.Combobox(
            selection, textvariable=self.selected, state="readonly", width=40
        )
        self.orders_box.pack(side="left", padx=10)
        ttk.Button(selection, text="查看确认订单", command=self.view_order).pack(side="left")
        self.delivery = DocumentEditor(audit_page)
        self.delivery.frame.pack(fill="both", expand=True)
        controls = ttk.Frame(audit_page, padding=10)
        controls.pack(fill="x")
        ttk.Button(
            controls, text="选择出库单照片并识别", command=lambda: self.extract(self.delivery)
        ).pack(side="left")
        ttk.Button(controls, text="核对字段后开始审核", command=self.audit).pack(
            side="left", padx=10
        )
        ttk.Button(controls, text="查看历史结果", command=self.history).pack(side="left")
        ttk.Label(
            wechat_page,
            text="仅限授权测试群。只读取你事先打开的独立群窗口；不下载图片、不发送、不进入正式审核。",
            wraplength=980,
        ).pack(anchor="w", pady=10)
        ttk.Label(
            wechat_page,
            text="上游仅声明支持微信 4.0.5，最小化可能收不到消息；许可证尚待维护者明确。请先用无客户数据的测试群。",
            wraplength=980,
        ).pack(anchor="w", pady=10)
        self.group = tk.StringVar()
        ttk.Label(wechat_page, text="唯一的测试群名称").pack(anchor="w")
        ttk.Entry(wechat_page, textvariable=self.group, width=50).pack(anchor="w", pady=8)
        ttk.Button(wechat_page, text="准备并校验诊断组件", command=self.prepare).pack(
            anchor="w", pady=8
        )
        ttk.Button(wechat_page, text="启动 120 秒只读诊断", command=self.start_probe).pack(
            anchor="w", pady=8
        )
        ttk.Button(wechat_page, text="停止本次诊断", command=self.stop_probe).pack(
            anchor="w", pady=8
        )
        footer = ttk.Frame(root, padding=10)
        footer.pack(fill="x")
        ttk.Button(footer, text="模型设置（Key 仅本次使用）", command=self.settings).pack(
            side="left"
        )
        ttk.Button(footer, text="打开本地数据目录", command=lambda: os.startfile(directory)).pack(
            side="left", padx=10
        )
        self.output = scrolledtext.ScrolledText(root, height=9, wrap="word")
        self.output.pack(fill="x", padx=12, pady=8)
        self.refresh_orders()
        root.protocol("WM_DELETE_WINDOW", self.close)

    def show(self, text):
        self.output.delete("1.0", "end")
        self.output.insert("end", text)

    def run(self, action, done):
        from tkinter import messagebox

        if self.busy:
            messagebox.showinfo("请稍候", "当前任务完成后再操作。")
            return
        self.busy = True
        self.status.set("正在处理，请稍候……")

        def work():
            try:
                value = action()
            except Exception as error:
                # Never include a provider response or API credential in a dialog/log.
                name = type(error).__name__
                self.root.after(0, lambda: finish(None, name))
            else:
                self.root.after(0, lambda: finish(value, None))

        def finish(value, error):
            self.busy = False
            self.status.set("任务结束 · 自动回群关闭")
            if error:
                messagebox.showerror(
                    "未完成",
                    f"处理未完成（{error}）。请核对网络、模型设置和材料后重试。没有批准发货。",
                )
            else:
                done(value)

        threading.Thread(target=work, daemon=True).start()

    def refresh_orders(self):
        ids = [row["order_id"] for row in self.service.orders()]
        self.orders_box["values"] = ids
        if ids and self.selected.get() not in ids:
            self.selected.set(ids[-1])

    def confirm(self):
        from tkinter import messagebox

        try:
            doc = self.order.document()
            self.service.confirm_order(doc.reference_order_id, doc)
            self.refresh_orders()
            self.show(f"已保存确认订单 {doc.reference_order_id}。请到“出库单审核”选择此订单。")
        except Exception:
            messagebox.showerror(
                "订单未保存", "请检查客户、唯一订单号、商品明细及数字。相同订单号不可覆盖。"
            )

    def audit(self):
        from tkinter import messagebox

        if self.busy:
            return
        try:
            report = self.service.audit(
                uuid.uuid4().hex, self.selected.get(), self.delivery.document(), "workbench"
            )
            titles = {
                "differences": "发现差异",
                "needs_confirmation": "资料待确认",
                "no_differences": "已识别字段无差异",
            }
            self.show(
                titles[report["status"]]
                + "\n"
                + "\n".join(f["message"] for f in report["findings"])
                + "\n结果已保存。本结果不代表批准发货，请核对原图和实物。"
            )
        except Exception:
            messagebox.showerror(
                "未完成审核", "请先选择已确认订单，检查出库单客户、关联订单号、商品明细与数字。"
            )

    def extract(self, editor):
        from tkinter import filedialog, messagebox

        from shipment_bot.extract import Extractor, validate_image

        if not self.key:
            messagebox.showinfo(
                "先设置模型", "照片识别需设置模型 API Key；也可不联网，直接手工填写商品明细。"
            )
            return
        chosen = filedialog.askopenfilename(
            title="选择完整清晰的单据照片",
            filetypes=[("单据图片", "*.png *.jpg *.jpeg *.webp *.bmp")],
        )
        if not chosen:
            return
        extractor = Extractor(self.key, base_url=self.base_url, model=self.model)
        path = Path(chosen)
        self.run(
            lambda: extractor.image(validate_image(path, [path.parent])),
            lambda doc: (
                editor.load(doc),
                self.show("已识别，请逐项核对并修正。订单号、客户和数字缺失时不会自动补齐。"),
            ),
        )

    def demo(self):
        doc = Document(
            customer="演示烘焙店",
            reference_order_id="DEMO001",
            lines=[
                Line(
                    name="蛋液",
                    specification="10kg",
                    quantity=Decimal("10"),
                    unit="箱",
                    unit_price=Decimal("85"),
                    price_unit="箱",
                    amount=Decimal("850"),
                )
            ],
            total=Decimal("850"),
        )
        self.order.load(doc)
        self.delivery.load(doc)
        self.show(
            "已载入演示材料：先保存确认订单，再到出库单审核。把出库数量从 10 改为 9，可测试差异提示。"
        )

    def view_order(self):
        rows = [r for r in self.service.orders() if r["order_id"] == self.selected.get()]
        if rows:
            self.order.load(Document.model_validate_json(rows[0]["document"]))
            self.show("确认订单已载入第一个页签供查看；原数据库中的已确认版本不会被覆盖。")

    def history(self):
        rows = self.service.results()
        self.show(
            "\n\n".join(
                r["order_id"]
                + " · "
                + r["created_at"]
                + "\n"
                + "\n".join(f["message"] for f in json.loads(r["report"])["findings"])
                + (
                    "已识别字段无差异，仍需人工核对"
                    if json.loads(r["report"])["status"] == "no_differences"
                    else ""
                )
                for r in rows[-20:]
            )
            or "暂无审核结果。"
        )

    def settings(self):
        import tkinter as tk
        from tkinter import ttk

        dialog = tk.Toplevel(self.root)
        dialog.title("模型设置")
        variables = [tk.StringVar(value=v) for v in (self.base_url, self.model, self.key)]

        def endpoint_changed(*_):
            if variables[0].get().strip().rstrip("/") != self.base_url.rstrip("/"):
                variables[2].set("")

        variables[0].trace_add("write", endpoint_changed)
        for i, (label, var) in enumerate(
            zip(("Base URL", "模型名", "API Key"), variables, strict=True)
        ):
            ttk.Label(dialog, text=label).grid(row=i, column=0, padx=12, pady=8)
            ttk.Entry(dialog, textvariable=var, width=50, show="●" if i == 2 else "").grid(
                row=i, column=1, padx=12, pady=8
            )
        ttk.Label(
            dialog, text="Key 仅保存在本次进程内。照片在本机 OCR，识别文字发送到所选模型。"
        ).grid(row=3, columnspan=2, padx=12, pady=8)

        def save():
            self.base_url, self.model, self.key = [v.get().strip() for v in variables]
            dialog.destroy()

        ttk.Button(dialog, text="本次使用", command=save).grid(row=4, column=1, pady=10)

    def prepare(self):
        from shipment_bot.wechat.wxauto4_download import download_bundle

        self.run(
            lambda: download_bundle(self.directory / "wxauto4-reviewed"),
            lambda _: self.show(
                "组件已准备并校验，没有连接微信。先手动打开测试群的独立窗口，再启动诊断。"
            ),
        )

    def start_probe(self):
        import subprocess
        from tkinter import messagebox

        from shipment_bot.wechat.wxauto4_prepare import verify

        if self.worker is not None and self.worker.poll() is None:
            return
        name = self.group.get().strip()
        if not name or "*" in name:
            messagebox.showerror("填写群名", "请输入名称唯一的授权测试群，不支持通配符。")
            return
        bundle = self.directory / "wxauto4-reviewed"
        try:
            verify(bundle)
        except Exception:
            messagebox.showerror("组件未就绪", "请先点击“准备并校验诊断组件”。")
            return
        args = [
            "--bundle",
            str(bundle),
            "--database",
            str(self.directory / "wxauto4-trial.sqlite3"),
            "--group",
            name,
            "--seconds",
            "120",
            "--live",
        ]
        command = (
            [sys.executable, "--wxauto4-probe", *args]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "shipment_bot.wechat.wxauto4_diagnostics", *args]
        )
        with (self.directory / "wxauto4-probe.log").open("a", encoding="utf-8") as log:
            self.worker = subprocess.Popen(
                command,
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        self.status.set("只读诊断中 · 120 秒 · 不发送消息")
        self.root.after(1000, self.check_probe)

    def check_probe(self):
        if self.worker is not None and self.worker.poll() is None:
            self.root.after(1000, self.check_probe)
        else:
            self.status.set("只读诊断已停止 · 自动回群关闭")
            path = self.directory / "wxauto4-probe.log"
            self.show(path.read_text(encoding="utf-8")[-6000:] if path.exists() else "诊断已停止。")

    def stop_probe(self):
        if self.worker is not None and self.worker.poll() is None:
            self.worker.terminate()

    def close(self):
        self.stop_probe()
        self.root.destroy()


def main():
    multiprocessing.freeze_support()
    if "--self-test" in sys.argv:
        return self_test()
    if "--sdk-import-test" in sys.argv:
        from shipment_bot.wechat.wxauto4_diagnostics import load_reviewed_sdk

        bundle = Path(sys.argv[sys.argv.index("--sdk-import-test") + 1])
        sdk = load_reviewed_sdk(bundle)
        assert hasattr(sdk, "WeChat")
        Path(os.environ["JIAHUI_SELF_TEST_REPORT"]).write_text(
            json.dumps({"sdk_imported": True, "wechat_connected": False}), encoding="utf-8"
        )
        return 0
    if "--wxauto4-probe" in sys.argv:
        from shipment_bot.wechat.wxauto4_diagnostics import main as probe

        sys.argv.remove("--wxauto4-probe")
        if sys.stdout is None:
            directory = data_directory()
            directory.mkdir(parents=True, exist_ok=True)
            sys.stdout = sys.stderr = (directory / "wxauto4-probe.log").open(
                "a", encoding="utf-8", buffering=1
            )
        try:
            probe()
            return 0
        except Exception as error:
            print(f"诊断未完成（{type(error).__name__}）：请检查独立群窗口、群名和微信版本。")
            return 1
    import tkinter as tk

    root = tk.Tk()
    Desktop(root, data_directory())
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
