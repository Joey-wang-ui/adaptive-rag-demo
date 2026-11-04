"""Offline account recovery for the owner of this Windows user's local data.

Never exposed through HTTP. Existing access scopes and disabled state are kept.
"""

from __future__ import annotations

from pathlib import Path

from app.jiahui.auth import AuthStore

ROLE_LABELS = {
    "admin": "管理员",
    "sales": "销售",
    "procurement": "采购",
    "finance": "财务",
    "warehouse": "仓储",
    "logistics": "物流",
}


def run_recovery(data_dir: Path, *, on_ready=None) -> bool:
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("嘉惠 · 本机账号恢复")
    root.geometry("470x505")
    root.resizable(False, False)
    root.configure(background="#f7f9f3")
    root.attributes("-topmost", True)
    font = ("Microsoft YaHei UI", 10)
    root.option_add("*Font", font)
    result = False
    if not (data_dir / "auth.sqlite3").is_file():
        messagebox.showinfo(
            "尚未创建账号",
            "请正常打开软件，先在首次使用页面创建自己的账号。软件没有默认密码。",
            parent=root,
        )
        root.destroy()
        return False
    store = AuthStore(data_dir)
    accounts = store.local_accounts()
    if not accounts:
        messagebox.showinfo("尚未创建账号", "请正常打开软件完成首次设置。", parent=root)
        root.destroy()
        return False
    outer = tk.Frame(root, background="#f7f9f3")
    outer.pack(fill="both", expand=True, padx=32, pady=26)
    tk.Label(
        outer,
        text="恢复本机账号",
        font=("Microsoft YaHei UI", 20, "bold"),
        background="#f7f9f3",
        foreground="#254f39",
    ).pack(anchor="w")
    tk.Label(
        outer,
        text="选择已创建的账号，设置一个新密码。\n资料、DeepSeek Key和岗位权限保持不变。",
        justify="left",
        background="#f7f9f3",
        foreground="#758471",
        pady=14,
    ).pack(anchor="w")
    tk.Label(outer, text="已有账号", background="#f7f9f3").pack(anchor="w", pady=(8, 7))
    account = tk.StringVar(
        value=next(
            (r["username"] for r in accounts if r["role"] == "admin" and r["active"]),
            accounts[0]["username"],
        )
    )
    selection = ttk.Combobox(
        outer, textvariable=account, values=[r["username"] for r in accounts], state="readonly"
    )
    selection.pack(fill="x", ipady=5)
    description = tk.StringVar()
    tk.Label(outer, textvariable=description, background="#f7f9f3", foreground="#758471").pack(
        anchor="w", pady=(7, 12)
    )

    def selected(*args):
        row = next(r for r in accounts if r["username"] == account.get())
        description.set(
            ROLE_LABELS.get(row["role"], row["role"])
            + (" · 已停用，请联系管理员" if not row["active"] else " · 已启用")
        )

    selection.bind("<<ComboboxSelected>>", selected)
    selected()
    password, confirmation = tk.StringVar(), tk.StringVar()
    for label, variable in [("新密码（至少12位）", password), ("再次输入新密码", confirmation)]:
        tk.Label(outer, text=label, background="#f7f9f3").pack(anchor="w", pady=(7, 6))
        ttk.Entry(outer, textvariable=variable, show="●").pack(fill="x", ipady=6)

    def save():
        nonlocal result
        if password.get() != confirmation.get():
            messagebox.showerror("密码不一致", "两次输入的密码不一致，请重新输入。", parent=root)
            return
        try:
            store.reset_password(account.get(), password.get())
        except ValueError as exc:
            messagebox.showerror("未能恢复", str(exc), parent=root)
            return
        password.set("")
        confirmation.set("")
        result = True
        messagebox.showinfo(
            "密码已更新",
            f"账号：{account.get()}\n旧登录已失效。请重新打开软件，使用刚设置的新密码登录。\n原资料与Key已保留，账号备份在资料目录的backups文件夹。",
            parent=root,
        )
        root.destroy()

    tk.Button(
        outer,
        text="保存新密码",
        command=save,
        background="#2b5b42",
        foreground="white",
        activebackground="#214630",
        activeforeground="white",
        relief="flat",
        pady=10,
    ).pack(fill="x", pady=(24, 0))
    if on_ready is not None:
        root.after(0, lambda: on_ready(root, account, password, confirmation, save))
    root.mainloop()
    return result


def inspect_accounts(directory: Path) -> list[dict]:
    """Read-only metadata inspection; this does not initialize or migrate a DB."""
    import sqlite3

    path = (directory / "auth.sqlite3").resolve()
    if not path.is_file():
        raise ValueError("该目录没有 auth.sqlite3，请选择原账号资料目录，不是软件安装目录。")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
        if not {"username", "role", "active", "salt", "password_hash", "scopes"}.issubset(columns):
            raise ValueError("该文件不是支持的嘉惠账号数据库，未修改任何内容。")
        return [
            {"username": row[0], "role": row[1], "active": bool(row[2])}
            for row in db.execute("SELECT username,role,active FROM users ORDER BY username")
        ]


def select_existing_data(home: Path, directory: Path) -> None:
    """Pin an operator-selected existing directory; never copy or reset accounts."""
    import json
    import os
    import shutil
    import tempfile
    import time

    directory = directory.expanduser().resolve()
    if not inspect_accounts(directory):
        raise ValueError("该目录还没有账号，请核对原资料目录。")
    home.mkdir(parents=True, exist_ok=True)
    target = home / "desktop.json"
    data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    if not isinstance(data, dict):
        raise ValueError("原桌面配置格式异常，请先核对备份，未覆盖。")
    if data.get("data_dir") == str(directory):
        return
    if target.exists():
        backup = home / "backups"
        backup.mkdir(exist_ok=True)
        shutil.copy2(target, backup / f"desktop-{time.time_ns()}.json")
    data["data_dir"] = str(directory)
    descriptor, name = tempfile.mkstemp(prefix="desktop-", suffix=".tmp", dir=home)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def run_data_selection(home: Path, current: Path) -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.withdraw()
    try:
        try:
            accounts = inspect_accounts(current)
            summary = (
                "、".join(
                    row["username"] + ("（已停用）" if not row["active"] else "")
                    for row in accounts
                )
                or "尚无账号"
            )
        except (ValueError, OSError):
            summary = "未找到支持的账号数据库"
        choose = messagebox.askyesno(
            "检查账号与资料目录",
            f"当前资料目录：\n{current}\n\n当前已有账号：{summary}\n\n如果与旧版账号不一致，可选择旧资料目录。是否选择？\n这不会重置密码，也不会移动、删除资料或Key。",
            parent=root,
        )
        if not choose:
            return
        selected = filedialog.askdirectory(
            title="选择包含原 auth.sqlite3 的资料文件夹", parent=root, mustexist=True
        )
        if not selected:
            return
        directory = Path(selected)
        accounts = inspect_accounts(directory)
        summary = "、".join(row["username"] for row in accounts) or "无账号"
        if not messagebox.askyesno(
            "确认原资料目录",
            f"将使用：\n{directory}\n\n已有账号：{summary}\n\n确认后重新打开软件，使用原账号和密码登录。原密码不会修改。",
            parent=root,
        ):
            return
        select_existing_data(home, directory)
        messagebox.showinfo(
            "资料目录已选择",
            "请重新打开软件，使用原账号和原密码登录。原资料、密码及Key均保留。",
            parent=root,
        )
    except Exception as exc:
        messagebox.showerror(
            "未切换目录",
            str(exc)
            if isinstance(exc, ValueError)
            else "资料目录检查未完成，未重置账号；请检查文件访问权限。",
            parent=root,
        )
    finally:
        root.destroy()
