"""Windows desktop UI and packaged-worker entry point. Never starts on open."""

import json
import multiprocessing
import os
import sys
from pathlib import Path

from shipment_bot.desktop_state import Controller, data_directory, save_settings

ROOT = Path(__file__).resolve().parent


def self_test():
    """Packaged runtime check. No account detection, API calls or message sending."""
    report = Path(os.environ["JIAHUI_SELF_TEST_REPORT"])
    try:
        import tkinter

        import wechatauto  # noqa: F401
        from shipment_bot.extract import Extractor

        Extractor("self-test-not-a-real-key").prepare_ocr()
        report.write_text(
            json.dumps(
                {"ocr_loaded": True, "tk_version": tkinter.TkVersion, "transport_imported": True}
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
        raise RuntimeError("桌面版需要 Windows 和已登录的电脑版微信。")
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
    root.title("嘉惠 · 微信群审单助手")
    root.geometry("900x740")
    root.minsize(800, 680)
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
    state = tk.StringVar(value="未启动 · 不会自动回复")
    identity = tk.StringVar(value="尚未检测账号与群，请先点击“检测连接”。")

    body = ttk.Frame(root, padding=28)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="嘉惠审单助手", font=("Microsoft YaHei UI", 23, "bold")).pack(anchor="w")
    ttk.Label(body, text="指定微信群 · @ 触发 · 自动回群", foreground="#547080").pack(
        anchor="w", pady=(5, 16)
    )
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
            ("DeepSeek API Key", key, True),
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

    def append(text):
        for secret in (key.get().strip(), saved_key, os.environ.get("DEEPSEEK_API_KEY", "")):
            if secret:
                text = text.replace(secret, "[Key 已隐藏]")
        log.configure(state="normal")
        log.insert("end", text)
        log.see("end")
        log.configure(state="disabled")

    def save():
        return save_settings(directory, group.get(), nickname.get(), key.get(), remember.get())

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

    controls = ttk.Frame(body)
    controls.pack(fill="x", pady=(0, 10))
    check_button = ttk.Button(controls, text="1  检测连接", command=lambda: launch("check"))
    check_button.pack(side="left")
    start_button = ttk.Button(
        controls, text="2  启动审单", style="Accent.TButton", command=lambda: launch("run")
    )
    start_button.pack(side="left", padx=10)
    stop_button = ttk.Button(controls, text="停止", command=controller.stop, state="disabled")
    stop_button.pack(side="left")
    ttk.Label(body, textvariable=identity, foreground="#547080", wraplength=820).pack(
        anchor="w", pady=(2, 10)
    )
    ttk.Label(
        body,
        text="群内用法：订单 A001 → 发图 → 出货单 A001 → 发图 → @审单bot 审核 A001",
        wraplength=820,
    ).pack(anchor="w")
    ttk.Label(
        body,
        text="每张图由发标签的同一人紧接着发送；普通消息不回复。审核结果不代表批准发货。",
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
        output, status = controller.poll()
        if output:
            append(output)
        labels = {
            "idle": "未启动 · 不会自动回复",
            "starting": "正在准备 / 连接…",
            "checked": "检测通过 · 尚未开启自动回复",
            "running": "运行中 · 指定群 @ 审核后自动回复",
            "stopping": "正在停止…",
            "stopped": "已停止 · 不再处理新审核",
            "error": "未能完成 · 请查看日志",
        }
        state.set(labels[controller.state])
        if status.get("account"):
            identity.set(
                f"账号：{status['account']}    群：{status.get('name', '')} ({status.get('group', '')})"
            )
        busy = controller.busy
        for widget in [*entries, remember_box, check_button, start_button]:
            widget.configure(state="disabled" if busy else "normal")
        stop_button.configure(state="normal" if busy and not controller.stopping else "disabled")
        if closing and not busy:
            root.destroy()
            return
        root.after(250, refresh)

    def close():
        nonlocal closing
        closing = True
        controller.stop()
        if not controller.busy:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(250, refresh)
    try:
        root.mainloop()
    finally:
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
