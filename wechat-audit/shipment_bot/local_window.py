"""Manual review window. UI callbacks never run OCR/API work or send to WeChat."""

import uuid
from pathlib import Path


class LocalReviewWindow:
    def __init__(self, root, controller, directory, save, get_key):
        import tkinter as tk
        from tkinter import scrolledtext, ttk

        self.controller, self.save, self.get_key = controller, save, get_key
        self.paths = []
        self.cache = directory / "window-images" / uuid.uuid4().hex
        self.last_result = None
        self.window = tk.Toplevel(root)
        self.window.title("窗口审核 · 无需微信连接")
        self.window.geometry(f"920x{max(650, min(820, root.winfo_screenheight() - 80))}")
        self.window.minsize(760, 650)
        panel = ttk.Frame(self.window, padding=18)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="窗口审核", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        ttk.Label(
            panel,
            text="选择电脑里的图片或粘贴截图，直接在这里看结果。无需群名或微信连接。",
            wraplength=850,
        ).pack(anchor="w", pady=6)
        ttk.Label(
            panel,
            text="图片在本机OCR，提取文字通过已有API设置审核；结果不自动回群。",
            foreground="#547080",
            wraplength=850,
        ).pack(anchor="w", pady=(0, 10))
        tools = ttk.Frame(panel)
        tools.pack(fill="x")
        self.add_button = ttk.Button(tools, text="选择图片（最多4张）", command=self.select_files)
        self.add_button.pack(side="left")
        self.paste_button = ttk.Button(tools, text="粘贴截图", command=self.paste_image)
        self.paste_button.pack(side="left", padx=8)
        self.clear_button = ttk.Button(tools, text="清空图片", command=self.clear_images)
        self.clear_button.pack(side="left")
        images = ttk.Frame(panel)
        images.pack(fill="x", pady=8)
        self.files = tk.Listbox(images, height=5, exportselection=False)
        self.files.pack(side="left", fill="both", expand=True)
        self.preview = ttk.Label(images, text="选择一张图片查看预览", width=32, anchor="center")
        self.preview.pack(side="right", padx=12)
        self.files.bind("<<ListboxSelect>>", lambda event: self.show_preview())
        self.files.bind("<Control-v>", lambda event: self.paste_image())
        ttk.Label(panel, text="待审核 / 对照文字（可选，可直接粘贴日常订单文字）").pack(anchor="w")
        self.text = scrolledtext.ScrolledText(
            panel, height=5, wrap="word", font=("Microsoft YaHei UI", 10)
        )
        self.text.pack(fill="x", pady=6)
        self.compare = tk.BooleanVar(value=True)
        self.compare_box = ttk.Checkbutton(
            panel, text="有一张图片和对照文字时核对图文是否一致", variable=self.compare
        )
        self.compare_box.pack(anchor="w")
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=10)
        self.start_button = ttk.Button(
            actions, text="开始窗口审核", style="Accent.TButton", command=self.start
        )
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="停止当前任务（Esc）", command=self.stop)
        self.stop_button.pack(side="left", padx=10)
        self.state = tk.StringVar(value="尚未开始")
        ttk.Label(panel, textvariable=self.state, wraplength=850).pack(anchor="w", pady=(0, 6))
        ttk.Label(panel, text="审核结果（可选中复制）").pack(anchor="w")
        self.result = scrolledtext.ScrolledText(
            panel, height=13, wrap="word", font=("Microsoft YaHei UI", 10), state="disabled"
        )
        self.result.pack(fill="both", expand=True, pady=6)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh({})

    def _error(self, error):
        from tkinter import messagebox

        messagebox.showerror("窗口审核", str(error), parent=self.window)

    def add_paths(self, paths):
        if self.controller.busy:
            return
        accepted = list(self.paths)
        for value in paths:
            path = Path(value).resolve(strict=True)
            if not path.is_file() or path.suffix.lower() not in {
                ".jpg",
                ".jpeg",
                ".jfif",
                ".png",
                ".webp",
                ".bmp",
            }:
                raise ValueError("请选择 JPG、PNG、WEBP 或 BMP 图片文件。")
            if path not in accepted:
                accepted.append(path)
        if len(accepted) > 4:
            raise ValueError("每次最多选择4张图片，请分批审核。")
        self.paths = accepted
        self.files.delete(0, "end")
        for path in self.paths:
            self.files.insert("end", path.name)
        if self.paths:
            self.files.selection_set(0)
            self.show_preview()

    def select_files(self):
        from tkinter import filedialog

        try:
            self.add_paths(
                filedialog.askopenfilenames(
                    parent=self.window,
                    title="选择本机单据图片",
                    filetypes=[("单据图片", "*.jpg *.jpeg *.jfif *.png *.webp *.bmp")],
                )
            )
        except (OSError, ValueError) as error:
            self._error(error)

    def paste_image(self):
        from PIL import Image, ImageGrab

        from shipment_bot.extract import MAX_PIXELS

        if self.controller.busy:
            return
        try:
            value = ImageGrab.grabclipboard()
            if isinstance(value, list):
                self.add_paths(value)
            elif isinstance(value, Image.Image):
                if len(self.paths) >= 4:
                    raise ValueError("每次最多4张图片，请先清空或分批审核。")
                if value.width * value.height > MAX_PIXELS:
                    raise ValueError("截图像素过大，请缩小后粘贴。")
                self.cache.mkdir(parents=True, exist_ok=True)
                path = self.cache / (uuid.uuid4().hex + ".png")
                value.save(path, format="PNG")
                self.add_paths([path])
            else:
                raise ValueError("剪贴板里没有图片。请先截图/复制图片，或点击“选择图片”。")
        except (OSError, ValueError, RuntimeError) as error:
            self._error(error)

    def clear_images(self):
        if not self.controller.busy:
            self.paths = []
            self.files.delete(0, "end")
            self.preview.configure(image="", text="尚未选择图片")
            self.preview.image = None

    def show_preview(self):
        from PIL import Image, ImageOps, ImageTk

        from shipment_bot.extract import MAX_PIXELS

        selected = self.files.curselection()
        if not selected:
            return
        try:
            with Image.open(self.paths[selected[0]]) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError("图片像素过大，请缩小后使用。")
                image = ImageOps.exif_transpose(image)
                image.thumbnail((245, 130))
                picture = ImageTk.PhotoImage(image, master=self.window)
            self.preview.configure(image=picture, text="")
            self.preview.image = picture
        except (OSError, ValueError) as error:
            self.preview.configure(image="", text="无法预览，请检查图片格式")
            self.preview.image = None
            self._error(error)

    def _set_result(self, value):
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.insert("1.0", value)
        self.result.configure(state="disabled")

    def start(self):
        from shipment_bot.local_review import validate_request

        try:
            request = {
                "images": [str(path) for path in self.paths],
                "text": self.text.get("1.0", "end").strip(),
                "compare": self.compare.get(),
            }
            validate_request(request)
            self.save()
            self.controller.start("local", self.get_key(), review=request)
            self._set_result("正在审核本次材料，请等待。可按Esc停止；结果不会自动发到微信。")
            self.refresh({})
        except (OSError, ValueError) as error:
            self._error(error)

    def stop(self):
        try:
            self.controller.stop()
        except OSError as error:
            self._error(error)

    def refresh(self, status):
        busy = self.controller.busy
        if busy and self.controller.mode != "local":
            text = "群任务正在运行，请先点击“停止当前任务”，再手动开始窗口审核。"
        elif busy:
            text = (
                "正在停止…"
                if self.controller.stopping
                else status.get("progress", "正在准备窗口审核…")
            )
        else:
            text = {
                "stopped": "已停止，不会自动恢复。需手动点击开始窗口审核。",
                "reviewed": "本次审核已结束，请查看结果。",
                "error": "任务未完成，请检查结果或主窗口日志。",
            }.get(self.controller.state, "选择图片或粘贴截图后，点击开始窗口审核。")
        self.state.set(text)
        for widget in (
            self.add_button,
            self.paste_button,
            self.clear_button,
            self.start_button,
            self.compare_box,
        ):
            widget.configure(state="disabled" if busy else "normal")
        self.text.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(
            state="normal" if busy and not self.controller.stopping else "disabled"
        )
        token = str(self.controller.statusfile)
        if self.controller.mode == "local" and not busy and token != self.last_result:
            if self.controller.state == "reviewed" and status.get("report"):
                self._set_result(status["report"])
                self.last_result = token
            elif self.controller.state == "error":
                self._set_result(status.get("error", "审核未完成，请查看主窗口日志。"))
                self.last_result = token
            elif self.controller.state == "stopped":
                self._set_result("本次任务已停止，没有完整审核结果。请手动重新开始。")
                self.last_result = token

    def close(self):
        if self.controller.busy and self.controller.mode == "local":
            self.stop()
        self.window.destroy()
