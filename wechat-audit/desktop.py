"""Windows desktop UI and packaged-worker entry point. Never starts on open."""

import json
import multiprocessing
import os
import sys
from pathlib import Path

from shipment_bot.audit_skill import builtin_skill
from shipment_bot.desktop_state import Controller, data_directory, save_settings

ROOT = Path(__file__).resolve().parent


def self_test():
    """Packaged runtime check. No account detection, API calls or message sending."""
    report = Path(os.environ["JIAHUI_SELF_TEST_REPORT"])
    try:
        import subprocess
        import tempfile
        import tkinter

        import imageio_ffmpeg
        from PIL import Image, ImageDraw, ImageFont
        from shipment_bot.extract import Extractor
        from shipment_bot.image_transport import check_synthetic_decryption
        from shipment_bot.windows_safety import GlobalEscape
        from wechatauto import MediaDownloader

        if len(builtin_skill()) < 300:
            raise RuntimeError("Bundled audit skill is missing or incomplete")
        hotkey = GlobalEscape()
        try:
            hotkey.start()
        finally:
            hotkey.close()
        extractor = Extractor("self-test-not-a-real-key")
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            sample = directory / "sample.jpg"
            image = Image.new("RGB", (1000, 240), "white")
            ImageDraw.Draw(image).text(
                (30, 65), "ORDER TOTAL 120", fill="black", font=ImageFont.truetype("arial.ttf", 64)
            )
            image.save(sample, quality=95)
            media, decoded = check_synthetic_decryption(
                MediaDownloader, sample.read_bytes(), directory
            )
            # Verify the bundled decoder, not an incidental system ffmpeg installation.
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            media._ffmpeg_exe = lambda: ffmpeg
            hevc = directory / "sample.hevc"
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(sample),
                    "-frames:v",
                    "1",
                    "-c:v",
                    "libx265",
                    "-threads",
                    "1",
                    "-x265-params",
                    "pools=none:frame-threads=1:log-level=error",
                    "-f",
                    "hevc",
                    str(hevc),
                ],
                capture_output=True,
                check=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            converted = media._wxgf_to_jpg(b"wxgf" + hevc.read_bytes())
            if not converted:
                raise RuntimeError("Bundled WXGF/HEVC decoder did not return a JPEG")
            sample.write_bytes(converted)
            recognized, _ = extractor.read_image(sample)
            if "120" not in recognized or "TOTAL" not in recognized.upper():
                raise RuntimeError("Bundled OCR did not recognize the decoded synthetic image")
            # Run the local-file route with real OCR and an in-memory API transport.
            import httpx
            from shipment_bot.local_review import review_request

            def synthetic_api(request):
                document = {
                    "total": "120",
                    "lines": [{"name": "TOTAL", "amount": "120", "evidence": recognized}],
                }
                return httpx.Response(
                    200, json={"choices": [{"message": {"content": json.dumps(document)}}]}
                )

            def local_factory(key, **kwargs):
                return Extractor(key, transport=httpx.MockTransport(synthetic_api), **kwargs)

            local_result = review_request(
                {"images": [str(sample)]}, {}, "self-test-only", factory=local_factory
            )
            if "第1张" not in local_result or "TOTAL" not in local_result:
                raise RuntimeError("Bundled local window review did not produce an image report")
        report.write_text(
            json.dumps(
                {
                    "ocr_loaded": True,
                    "audit_skill_loaded": True,
                    "local_file_review": True,
                    "ocr_image_recognized": True,
                    "synthetic_v2_decrypted": True,
                    "wxgf_hevc_decoded": True,
                    "tk_version": tkinter.TkVersion,
                    "transport_imported": True,
                    "global_esc_registered_and_released": True,
                }
            ),
            encoding="utf-8",
        )
        return 0
    except Exception as error:
        report.write_text(
            json.dumps({"error": type(error).__name__, "detail": str(error)}), encoding="utf-8"
        )
        return 1


def worker(mode):
    """Windowed executables have no stdout: explicitly open the private worker log."""
    path = Path(os.environ["JIAHUI_AUDIT_LOG"])
    with path.open("a", encoding="utf-8", buffering=1) as stream:
        sys.stdout = sys.stderr = stream
        try:
            if mode == "local":
                from shipment_bot import local_review

                local_review.main()
            else:
                from shipment_bot import group_runtime

                sys.argv = [sys.argv[0], mode]
                group_runtime.main()
            return 0
        except Exception as error:
            # Avoid traceback-local dumps and never log the configured API credential.
            text = str(error)
            secret = os.environ.get("DEEPSEEK_API_KEY", "")
            if secret:
                text = text.replace(secret, "[Key 已隐藏]")
            print(f"启动未完成（{type(error).__name__}）：{text}")
            return 1


def main():
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, ttk

    if os.name != "nt":
        raise RuntimeError("桌面版需要 Windows；窗口审核无需微信，群审单另需登录电脑版微信。")
    directory = data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    import msvcrt

    lock = (directory / "desktop.lock").open("a+b")
    lock.write(b"0")
    lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        messagebox.showinfo("嘉惠审单助手", "软件已打开，请切换到已有窗口。")
        return

    root = tk.Tk()
    root.title("嘉惠 · 微信群审单助手 v0.9 · 窗口审核")
    root.geometry("900x790")
    root.minsize(800, 730)
    root.configure(bg="#f4f7fb")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TFrame", background="#f4f7fb")
    style.configure("TLabel", background="#f4f7fb", font=("Microsoft YaHei UI", 10))
    style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(16, 9))
    style.configure("Accent.TButton", background="#087f72", foreground="white")
    style.configure("TCheckbutton", background="#f4f7fb", font=("Microsoft YaHei UI", 9))
    style.configure("TEntry", padding=7, font=("Microsoft YaHei UI", 10))
    controller = Controller(directory, ROOT)
    closing = False
    review_window = None
    config_path = directory / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    except (ValueError, OSError):
        config = {}
    key_path = directory / "api-key.txt"
    saved_key = key_path.read_text(encoding="utf-8").strip() if key_path.exists() else ""
    group = tk.StringVar(value=config.get("group_name", ""))
    nickname = tk.StringVar(value=config.get("bot_nickname", "审单bot"))
    key = tk.StringVar(value=saved_key or os.environ.get("DEEPSEEK_API_KEY", ""))
    remember = tk.BooleanVar(value=bool(saved_key))
    allow_self_test = tk.BooleanVar(value=config.get("allow_self_test") is True)
    state = tk.StringVar(value="未启动 · 不会自动回复")
    identity = tk.StringVar(value="尚未检测账号与群，请先点击“检测连接”。")
    api_url = tk.StringVar(value=config.get("llm", {}).get("base_url", "https://api.deepseek.com"))
    model_name = tk.StringVar(value=config.get("llm", {}).get("model", "deepseek-chat"))
    instructions = config.get("audit", {}).get("instructions", "")
    skill_text = config.get("audit", {}).get("skill", "")
    require_customer = tk.BooleanVar(value=config.get("audit", {}).get("require_customer", False))
    require_specification = tk.BooleanVar(
        value=config.get("audit", {}).get("require_specification", False)
    )

    body = ttk.Frame(root, padding=28)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="嘉惠审单助手", font=("Microsoft YaHei UI", 23, "bold")).pack(anchor="w")
    ttk.Label(
        body,
        text="指定微信群 · @ 触发 · 自动回群 · 任务运行时按 Esc 全局急停",
        foreground="#547080",
    ).pack(anchor="w", pady=(5, 16))
    badge = ttk.Label(
        body, textvariable=state, foreground="#087f72", font=("Microsoft YaHei UI", 12, "bold")
    )
    badge.pack(anchor="w", pady=(0, 12))
    form = ttk.Frame(body)
    form.pack(fill="x")
    form.columnconfigure(1, weight=1)
    entries = []
    for row, (label, variable, secret) in enumerate(
        [
            ("指定群名称", group, False),
            ("机器人群昵称", nickname, False),
            ("模型 API Key", key, True),
        ]
    ):
        ttk.Label(form, text=label, width=20).grid(row=row, column=0, sticky="w", pady=5)
        entry = ttk.Entry(form, textvariable=variable, show="●" if secret else "")
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        entries.append(entry)
    remember_box = ttk.Checkbutton(
        body, text="记住 API Key（仅本机明文保存，请勿分享数据目录）", variable=remember
    )
    remember_box.pack(anchor="w", pady=(6, 12))
    self_test_box = ttk.Checkbutton(
        body,
        text="允许自己触发测试（默认关闭；仅指定群，手动输入 @群昵称 审核）",
        variable=allow_self_test,
    )
    self_test_box.pack(anchor="w", pady=(0, 12))

    def append(text):
        for secret in (key.get().strip(), saved_key, os.environ.get("DEEPSEEK_API_KEY", "")):
            if secret:
                text = text.replace(secret, "[Key 已隐藏]")
        log.configure(state="normal")
        log.insert("end", text)
        log.see("end")
        log.configure(state="disabled")

    def save(local=False):
        return save_settings(
            directory,
            group.get(),
            nickname.get(),
            key.get(),
            remember.get(),
            allow_self_test=allow_self_test.get(),
            require_group=not local,
            llm={"base_url": api_url.get(), "model": model_name.get()},
            audit={
                "instructions": instructions,
                "skill": skill_text,
                "require_customer": require_customer.get(),
                "require_specification": require_specification.get(),
            },
        )

    def settings():
        nonlocal instructions, skill_text
        dialog = tk.Toplevel(root)
        dialog.title("模型与审核设置")
        dialog.geometry("760x680")
        skill_var = tk.StringVar(value=skill_text or builtin_skill())
        dialog.transient(root)
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=24)
        panel.pack(fill="both", expand=True)
        url_var, model_var = tk.StringVar(value=api_url.get()), tk.StringVar(value=model_name.get())
        customer_var = tk.BooleanVar(value=require_customer.get())
        spec_var = tk.BooleanVar(value=require_specification.get())
        for label, variable in [
            ("接口地址 Base URL（OpenAI 兼容）", url_var),
            ("模型名（按平台填写）", model_var),
        ]:
            ttk.Label(panel, text=label).pack(anchor="w", pady=(0, 4))
            ttk.Entry(panel, textvariable=variable).pack(fill="x", pady=(0, 12))
        ttk.Label(
            panel,
            text="支持文字 Chat Completions + JSON 输出；不直接支持 Anthropic 等原生协议。",
            foreground="#547080",
            wraplength=640,
        ).pack(anchor="w", pady=(0, 12))
        ttk.Checkbutton(panel, text="客户名称缺失时也提醒", variable=customer_var).pack(anchor="w")
        ttk.Checkbutton(panel, text="规格缺失时也提醒", variable=spec_var).pack(anchor="w")
        ttk.Label(panel, text="补充审核 / 提取要求（可改商品简称、关注点；不能替代单据证据）").pack(
            anchor="w", pady=(14, 6)
        )
        editor = scrolledtext.ScrolledText(panel, height=6, font=("Microsoft YaHei UI", 10))
        editor.insert("1.0", instructions)
        editor.pack(fill="both", expand=True)
        ttk.Label(
            panel, text="金额计算、单位不清提示和不自动批准发货始终保留。", foreground="#547080"
        ).pack(anchor="w", pady=8)

        def edit_skill():
            window = tk.Toplevel(dialog)
            window.title("完整审单技能")
            window.geometry("850x720")
            window.transient(dialog)
            window.grab_set()
            ttk.Label(
                window,
                text="正常说法即可审单；规则可修改。金额计算、原文证据与Esc急停由程序执行。",
                wraplength=800,
            ).pack(padx=16, pady=12)
            content = scrolledtext.ScrolledText(
                window, wrap="word", font=("Microsoft YaHei UI", 10)
            )
            content.insert("1.0", skill_var.get())
            content.pack(fill="both", expand=True, padx=16)

            def accept():
                value = content.get("1.0", "end").strip()
                if not value or len(value) > 20000:
                    messagebox.showerror(
                        "无法保存", "技能内容不能为空，最多20000字。", parent=window
                    )
                    return
                skill_var.set(value)
                window.destroy()

            buttons = ttk.Frame(window, padding=12)
            buttons.pack(fill="x")

            def restore():
                content.delete("1.0", "end")
                content.insert("1.0", builtin_skill())

            ttk.Button(buttons, text="恢复内置技能", command=restore).pack(side="left")
            ttk.Button(buttons, text="使用此技能", command=accept).pack(side="right")

        ttk.Button(panel, text="查看 / 编辑完整审单技能", command=edit_skill).pack(
            anchor="w", pady=8
        )

        def apply():
            nonlocal instructions, skill_text
            old = (
                api_url.get(),
                model_name.get(),
                instructions,
                require_customer.get(),
                require_specification.get(),
            )
            old_skill = skill_text
            old_key, old_remember = key.get(), remember.get()
            skill_text = skill_var.get()
            api_url.set(url_var.get())
            model_name.set(model_var.get())
            instructions = editor.get("1.0", "end").strip()
            require_customer.set(customer_var.get())
            require_specification.set(spec_var.get())
            changed_endpoint = api_url.get().strip().rstrip("/") != old[0].strip().rstrip("/")
            if changed_endpoint:
                key.set("")
                remember.set(False)
            try:
                save(local=True)
            except (ValueError, OSError) as error:
                api_url.set(old[0])
                model_name.set(old[1])
                instructions = old[2]
                skill_text = old_skill
                require_customer.set(old[3])
                require_specification.set(old[4])
                key.set(old_key)
                remember.set(old_remember)
                messagebox.showerror("无法保存", str(error), parent=dialog)
                return
            dialog.destroy()
            if changed_endpoint:
                messagebox.showinfo(
                    "已切换接口",
                    "请在主界面填写新平台的 API Key。旧 Key 已清除，不会自动转发给新平台。",
                    parent=root,
                )

        ttk.Button(panel, text="保存设置", command=apply).pack(anchor="e", pady=(8, 0))

    def launch(mode):
        try:
            save()
            controller.start(mode, key.get())
            append(
                "\n—— 开始检测连接（不发消息） ——\n"
                if mode == "check"
                else "\n—— 开始启动审单；等待群监听就绪 ——\n"
            )
        except (ValueError, OSError) as error:
            messagebox.showerror("请检查配置", str(error), parent=root)

    def stop():
        try:
            controller.stop()
        except OSError as error:
            messagebox.showerror("急停未完成", str(error), parent=root)

    def open_review():
        nonlocal review_window
        from shipment_bot.local_window import LocalReviewWindow

        if review_window is not None and review_window.window.winfo_exists():
            review_window.window.deiconify()
            review_window.window.lift()
            return
        review_window = LocalReviewWindow(
            root, controller, directory, lambda: save(local=True), key.get
        )

    manual = ttk.Frame(body)
    manual.pack(fill="x", pady=(0, 10))
    ttk.Button(
        manual, text="窗口审核 · 选图片 / 粘贴截图", style="Accent.TButton", command=open_review
    ).pack(side="left")
    ttk.Label(manual, text="无需微信连接，结果显示在窗口，不自动回群。", foreground="#547080").pack(
        side="left", padx=10
    )
    controls = ttk.Frame(body)
    controls.pack(fill="x", pady=(0, 10))
    check_button = ttk.Button(controls, text="1  检测连接", command=lambda: launch("check"))
    check_button.pack(side="left")
    start_button = ttk.Button(
        controls, text="2  启动审单", style="Accent.TButton", command=lambda: launch("run")
    )
    start_button.pack(side="left", padx=10)
    stop_button = ttk.Button(controls, text="停止", command=stop, state="disabled")
    stop_button.pack(side="left")
    settings_button = ttk.Button(controls, text="模型与审核设置", command=settings)
    settings_button.pack(side="right")
    ttk.Label(body, textvariable=identity, foreground="#547080", wraplength=820).pack(
        anchor="w", pady=(2, 10)
    )
    ttk.Label(
        body,
        text="群内用法：发订单文字、单据图片，或图文混发 → @审单bot 审核（不必填标签或编号）",
        wraplength=820,
    ).pack(anchor="w")
    ttk.Label(
        body,
        text="每批最多4份材料；默认取本人近期材料。也可首行 @审单bot 审核，换行粘贴文字。普通消息不回复。",
        foreground="#547080",
        wraplength=820,
    ).pack(anchor="w", pady=(4, 12))
    ttk.Label(body, text="运行日志", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
    log = scrolledtext.ScrolledText(
        body,
        height=10,
        bg="#ffffff",
        fg="#253b49",
        relief="flat",
        font=("Consolas", 10),
        padx=12,
        pady=10,
        state="disabled",
    )
    log.pack(fill="both", expand=True, pady=(6, 10))
    footer = ttk.Frame(body)
    footer.pack(fill="x")
    ttk.Label(footer, text="试运行版 · 微信 4.2.0.6 尚待实机验证", foreground="#8a641f").pack(
        side="left"
    )
    ttk.Button(footer, text="打开数据目录", command=lambda: os.startfile(directory)).pack(
        side="right"
    )

    def refresh():
        try:
            output, status = controller.poll()
        except OSError as error:
            append(f"急停 / 生命周期错误：{error}\n")
            state.set("停止未完成 · 请点击停止重试")
            root.after(50, refresh)
            return
        if output:
            append(output)
        labels = {
            "idle": "未启动 · 不会自动回复",
            "starting": "正在准备 / 连接…",
            "checked": "检测通过 · 尚未开启自动回复",
            "running": "运行中 · 指定群 @ 审核后自动回复",
            "stopping": "正在停止…",
            "stopped": "已急停 · 自动操作已终止；需手动点击启动审单",
            "error": "未能完成 · 请查看日志",
            "reviewed": "窗口审核已结束 · 请查看审核结果",
        }
        if controller.mode == "local":
            labels.update(
                starting="正在准备窗口审核…",
                running="窗口审核进行中 · 不访问微信、不回群",
                stopped="窗口审核已停止 · 不会自动恢复",
            )
        state.set(labels[controller.state])
        if status.get("account"):
            identity.set(
                f"账号：{status['account']}    群：{status.get('name', '')} ({status.get('group', '')})"
            )
        if review_window is not None and review_window.window.winfo_exists():
            review_window.refresh(status)
        busy = controller.busy
        for widget in [
            *entries,
            remember_box,
            self_test_box,
            check_button,
            start_button,
            settings_button,
        ]:
            widget.configure(state="disabled" if busy else "normal")
        stop_button.configure(state="normal" if busy and not controller.stopping else "disabled")
        if closing and not busy:
            root.destroy()
            return
        root.after(50, refresh)

    def close():
        nonlocal closing
        closing = True
        stop()
        if not controller.busy:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(50, refresh)
    try:
        root.mainloop()
    finally:
        controller.close()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        lock.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if sys.argv[1:2] == ["--self-test"]:
        sys.exit(self_test())
    if sys.argv[1:2] == ["--worker"]:
        sys.exit(worker(sys.argv[2]))
    main()
