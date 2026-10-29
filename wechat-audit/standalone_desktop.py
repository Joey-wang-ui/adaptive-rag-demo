"""Standalone shipment audit. Opening the window never calls a model or WeChat."""

import multiprocessing
import sys


def self_test():
    """Packaged Windows OCR + Tk + DPAPI check, without network or real credentials."""
    import json
    import os
    import tempfile
    import tkinter as tk
    from pathlib import Path

    from PIL import Image, ImageDraw, ImageFont
    from shipment_bot.extract import Extractor
    from shipment_bot.standalone_settings import protect

    report = Path(os.environ["JIAHUI_STANDALONE_TEST_REPORT"])
    try:
        root = tk.Tk()
        root.withdraw()
        root.update()
        root.destroy()
        with tempfile.TemporaryDirectory() as folder:
            image = Image.new("RGB", (900, 240), "white")
            draw = ImageDraw.Draw(image)
            font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 48)
            draw.text((30, 40), "TOTAL 100", font=font, fill="black")
            path = Path(folder) / "ocr.png"
            image.save(path)
            text, _ = Extractor("self-test-dummy").read_image(path)
            if "100" not in text or "TOTAL" not in text.upper():
                raise RuntimeError("Packaged OCR did not recognize expected fixture")
        assert protect(protect(b"dummy-self-test"), decrypt=True) == b"dummy-self-test"
        import time

        from shipment_bot.standalone import AuditTask, Material

        task = AuditTask()
        try:
            task.start([Material("fixture")], {"api_key": ""})
            deadline = time.monotonic() + 15
            outcome = None
            while outcome is None and time.monotonic() < deadline:
                outcome = task.poll()
                time.sleep(0.02)
            assert outcome and outcome[0] == "error" and "Key" in outcome[1]
        finally:
            task.stop()
        forbidden = (
            "wechatauto",
            "pyautogui",
            "uiautomation",
            "shipment_bot.group_runtime",
            "shipment_bot.group_audit",
            "shipment_bot.group_ui",
        )
        assert not any(x in sys.modules for x in forbidden)
        result = {
            "ok": True,
            "tk": True,
            "ocr_fixture": True,
            "dpapi": True,
            "spawned_worker": True,
            "wechat_imported": False,
            "real_api_tested": False,
        }
        status = 0
    except Exception as error:
        result, status = {"ok": False, "error_type": type(error).__name__}, 1
    report.write_text(json.dumps(result), encoding="utf-8")
    return status


def main():
    import tkinter as tk
    from copy import deepcopy
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    from shipment_bot.standalone import AuditTask, Material
    from shipment_bot.standalone_settings import directory, load, save

    root = tk.Tk()
    root.title("嘉惠食品 · 独立发货前审单")
    root.geometry("1060x880")
    root.minsize(900, 730)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(6, weight=1)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TButton", padding=7)
    style.configure("TLabel", font=("Microsoft YaHei UI", 10))
    style.configure("Title.TLabel", font=("Microsoft YaHei UI", 17, "bold"))
    ttk.Label(root, text="嘉惠食品 · 发货前辅助审核", style="Title.TLabel").grid(
        sticky="w", padx=16, pady=10
    )
    ttk.Label(
        root, text="图片 OCR 在本机运行；点击开始才向所配置服务发送文字。结果不能自动批准发货。"
    ).grid(sticky="w", padx=16)
    path = directory()
    try:
        config, saved_key = load(path)
    except (OSError, ValueError):
        config, saved_key = {}, ""
        messagebox.showwarning("配置", "本机配置无法读取，请重新填写；未调用 API。")
    base = tk.StringVar(value=config.get("base_url", "https://api.deepseek.com"))
    model = tk.StringVar(value=config.get("model", "deepseek-chat"))
    key = tk.StringVar(value=saved_key)
    remember = tk.BooleanVar(value=bool(saved_key))
    previous_base = [base.get()]

    def endpoint_changed(*_):
        if base.get() != previous_base[0]:
            key.set("")  # Never transfer a stored credential to another provider.
            previous_base[0] = base.get()

    base.trace_add("write", endpoint_changed)
    settings = ttk.Frame(root, padding=12)
    settings.grid(sticky="ew")
    settings.columnconfigure(1, weight=1)
    for row, (label, variable) in enumerate(
        (("Base URL", base), ("模型", model), ("API Key", key))
    ):
        ttk.Label(settings, text=label).grid(row=row, column=0, sticky="w", padx=4)
        ttk.Entry(settings, textvariable=variable, show="*" if row == 2 else "").grid(
            row=row, column=1, sticky="ew", pady=3
        )
    ttk.Checkbutton(
        settings, text="仅在本机加密保存 Key（Windows 当前用户）", variable=remember
    ).grid(row=3, column=1, sticky="w")
    ttk.Label(settings, text="补充审核要求（不能关闭金额核算或自动批准发货）").grid(
        row=4, column=0, columnspan=2, sticky="w"
    )
    instructions = scrolledtext.ScrolledText(settings, height=2, wrap="word")
    instructions.grid(row=5, column=0, columnspan=2, sticky="ew")
    instructions.insert("1.0", config.get("instructions", ""))
    material_frame = ttk.LabelFrame(
        root, text="材料：每份可混合文字和多张图片；不同单据请分别添加", padding=10
    )
    material_frame.grid(sticky="ew", padx=12)
    material_frame.columnconfigure(0, weight=1)
    materials, images = [], []
    roles = {
        "自动判断 / 不确定": "unknown",
        "订单（人工指定）": "order",
        "出货单（人工指定）": "delivery",
    }
    role = tk.StringVar(value=next(iter(roles)))
    ttk.Combobox(
        material_frame, textvariable=role, values=list(roles), state="readonly", width=24
    ).grid(row=0, column=0, sticky="w")
    text = scrolledtext.ScrolledText(material_frame, height=4, wrap="word")
    text.grid(row=1, column=0, columnspan=3, sticky="ew", pady=5)
    image_label = ttk.Label(material_frame, text="未添加图片")
    image_label.grid(row=2, column=0, columnspan=3, sticky="w")
    listing = tk.Listbox(material_frame, height=3, exportselection=False)
    listing.grid(row=4, column=0, columnspan=3, sticky="ew", pady=4)
    task = AuditTask()
    status = tk.StringVar(value="待添加材料；打开软件不会调用模型。")

    def add_images():
        if task.busy:
            return
        selected = filedialog.askopenfilenames(
            filetypes=[("单据图片", "*.png *.jpg *.jpeg *.webp *.bmp")]
        )
        if len(images) + len(selected) > 4:
            messagebox.showwarning("材料", "每份最多 4 张图片，请拆分。")
            return
        images.extend(selected)
        image_label.configure(text="\n".join(images) or "未添加图片")

    def reset_editor():
        images.clear()
        text.delete("1.0", "end")
        image_label.configure(text="未添加图片")

    def add_material():
        if task.busy:
            return False
        content = text.get("1.0", "end").strip()
        if not content and not images:
            return False
        if len(materials) >= 8 or len(content) > 20000:
            messagebox.showwarning("材料", "最多 8 份材料，每份文字不超过 20000 字。")
            return False
        materials.append(Material(content, list(images), roles[role.get()]))
        listing.insert(
            "end", f"材料 {len(materials)} · {role.get()} · {len(content)} 字 / {len(images)} 张图"
        )
        reset_editor()
        return True

    ttk.Button(material_frame, text="上传图片", command=add_images).grid(row=0, column=1)
    ttk.Button(material_frame, text="添加为一份材料", command=add_material).grid(row=0, column=2)
    buttons = ttk.Frame(root, padding=10)
    buttons.grid(sticky="ew")
    output = scrolledtext.ScrolledText(root, wrap="word", state="disabled")
    output.grid(row=6, column=0, sticky="nsew", padx=12)
    ttk.Label(root, textvariable=status, wraplength=1000).grid(row=7, sticky="w", padx=12, pady=8)
    ttk.Label(
        root,
        text="停止 / Esc 可取消任务，窗口保持打开；已发出的 API 请求可能仍产生费用。关闭窗口也会停止任务。",
    ).grid(row=8, sticky="w", padx=12, pady=8)

    def display(value):
        output.configure(state="normal")
        output.delete("1.0", "end")
        output.insert("1.0", value)
        output.configure(state="disabled")

    def start():
        if task.busy:
            return
        add_material()
        if text.get("1.0", "end").strip() or images:
            return  # Invalid pending material must never silently disappear.
        if not materials or not key.get().strip():
            messagebox.showwarning("审核", "请添加材料并填写 API Key。")
            return
        config = {
            "base_url": base.get().strip(),
            "model": model.get().strip(),
            "instructions": instructions.get("1.0", "end").strip()[:3000],
        }
        try:
            from shipment_bot.extract import Extractor

            Extractor(key.get().strip(), **config)  # Validate locally; no network call.
            save(path, config, key.get().strip(), remember.get())
            task.start(deepcopy(materials), {**config, "api_key": key.get().strip()})
        except Exception:
            messagebox.showerror(
                "无法开始", "请检查接口地址、模型、Key 及本机保存权限；未产生有效审核。"
            )
            return
        display("审核中……")
        status.set("审核中：OCR → 字段提取 → 固定金额核算。可停止或按 Esc。")

    def stop(event=None):
        if task.busy:
            task.stop()
            display("本次审核已取消，未产生有效审核结果。已发出的 API 请求可能仍产生费用。")
            status.set("已取消，可修改材料并重新审核。")

    def clear():
        stop()
        materials.clear()
        listing.delete(0, "end")
        reset_editor()
        display("")
        root.clipboard_clear()
        status.set("材料和结果已清空；API 配置保留。")

    def copy():
        root.clipboard_clear()
        root.clipboard_append(output.get("1.0", "end").strip())
        status.set("结果已复制，包含单据原文，请注意业务资料保密。")

    def poll():
        result = task.poll()
        if result:
            kind, value = result
            display(value if kind == "ok" else "审核未完成：" + value + "\n没有给出发货许可。")
            status.set(
                "审核完成，仍须人工确认。" if kind == "ok" else "审核失败，可修改配置或材料后重试。"
            )
        root.after(100, poll)

    def close():
        task.stop()
        root.destroy()

    for label, action in (
        ("开始审核", start),
        ("停止审核 (Esc)", stop),
        ("复制结果", copy),
        ("清空材料", clear),
    ):
        ttk.Button(buttons, text=label, command=action).pack(side="left", padx=5)
    root.bind("<Escape>", stop)
    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, poll)
    root.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    main()
