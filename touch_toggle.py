# -*- coding: utf-8 -*-
"""
触摸屏快速切换工具 (Windows, 零第三方依赖)

功能:
  - 一键启用 / 禁用所有触摸屏设备(自动排除触摸板/鼠标等非触摸屏设备)
  - 全局热键 Ctrl+Alt+T 随时快速切换
  - 支持对单个设备单独切换(双击列表行或"切换选中")
  - 迷你模式:一个小置顶窗口,只放一个大按钮
  - 可注册为开机自启(任务计划,最高权限,登录时不再弹 UAC)

用法:
  python touch_toggle.py            # 首次运行会弹一次 UAC 提权
  pythonw touch_toggle.py           # 不显示控制台窗口
  python touch_toggle.py --mini     # 直接以迷你模式启动

注意: 禁用 / 启用 PnP 设备需要管理员权限,程序会自动提权重启自身。
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk

APP_TITLE = "触摸屏快速切换"
HOTKEY_TEXT = "Ctrl+Alt+T"
TASK_NAME = "TouchScreenToggle"

CREATE_NO_WINDOW = 0x08000000
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_T = 0x54

PS_TIMEOUT = 60


# ---------------------------------------------------------------------------
# 系统辅助
# ---------------------------------------------------------------------------

def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_admin() -> bool:
    if not is_windows():
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate() -> None:
    """以管理员权限重启自身(会弹一次 UAC),并转发原始命令行参数。"""
    script = os.path.abspath(sys.argv[0])
    args = " ".join('"%s"' % a for a in sys.argv[1:])
    params = ('"%s" %s' % (script, args)).strip()
    workdir = os.path.dirname(script) or None
    res = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, workdir, 1
    )
    if res <= 32:  # UAC 被取消或启动失败
        ctypes.windll.user32.MessageBoxW(
            0,
            "需要管理员权限才能禁用/启用触摸设备,已取消启动。",
            APP_TITLE,
            0x30,  # MB_ICONWARNING
        )
    sys.exit(0)


def _run_ps(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NoLogo",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=PS_TIMEOUT,
        creationflags=CREATE_NO_WINDOW,
    )


# ---------------------------------------------------------------------------
# 触摸设备枚举与开关
# ---------------------------------------------------------------------------

def list_touch_devices() -> list[dict]:
    """返回当前存在的触摸屏设备列表(不含触摸板/鼠标)。"""
    script = r"""
    $devs = Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object {
        ($_.FriendlyName -match 'touch|digitizer|触摸|触控|手写') -or
        ($_.HardwareId -match 'UP:000D_U:0004')
    } | Where-Object {
        $_.FriendlyName -notmatch 'pad|mouse|pointer|trackpoint|keyboard|触摸板|触控板'
    } | Select-Object InstanceId, FriendlyName, Status, Class
    $devs | ConvertTo-Json -Compress -Depth 3
    """
    try:
        r = _run_ps(script)
    except Exception as exc:
        raise RuntimeError("枚举设备失败: %s" % exc) from exc

    raw = (r.stdout or "").strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    devices = []
    for item in data:
        status = item.get("Status", "") or ""
        devices.append(
            {
                "instance_id": item.get("InstanceId", ""),
                "name": item.get("FriendlyName", ""),
                "status": status,
                "class": item.get("Class", ""),
                "enabled": status == "OK",
            }
        )
    return devices


def set_device_enabled(instance_id: str, enable: bool) -> tuple[bool, str]:
    """启用/禁用单个 PnP 设备。返回 (是否成功, 错误信息)。"""
    action = "Enable-PnpDevice" if enable else "Disable-PnpDevice"
    script = (
        "$id = '%s'; "
        "try { %s -InstanceId $id -Confirm:$false -ErrorAction Stop; 'OK' } "
        "catch { 'ERR:' + $_.Exception.Message }"
        % (instance_id, action)
    )
    try:
        r = _run_ps(script)
    except Exception as exc:
        return False, str(exc)
    out = (r.stdout or "").strip()
    if out.startswith("OK"):
        return True, ""
    return False, out or (r.stderr or "").strip() or "未知错误"


# ---------------------------------------------------------------------------
# 开机自启(任务计划,最高权限)
# ---------------------------------------------------------------------------

def task_exists() -> bool:
    r = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME],
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    return r.returncode == 0


def set_autostart(enable: bool) -> tuple[bool, str]:
    script = os.path.abspath(sys.argv[0])
    tr = '"%s" "%s"' % (sys.executable, script)
    if enable:
        cmd = [
            "schtasks", "/Create", "/TN", TASK_NAME, "/TR", tr,
            "/SC", "ONLOGON", "/RL", "HIGHEST", "/F",
        ]
    else:
        cmd = ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW
        )
    except Exception as exc:
        return False, str(exc)
    if r.returncode == 0:
        return True, ""
    return False, (r.stdout + r.stderr).strip() or "schtasks 返回 %d" % r.returncode


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class TouchToggleApp(tk.Tk):
    def __init__(self, start_mini: bool = False) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("600x430")
        self.minsize(500, 340)
        self.attributes("-topmost", True)

        self.devices: list[dict] = []
        self._row_instance: dict[str, str] = {}  # 列表行 item -> InstanceId
        self._hotkey_down = False
        self._mini = False

        self._build_ui()
        if start_mini:
            self._set_mini(True)
        self.refresh_devices()
        self._refresh_autostart_label()
        self.after(100, self._poll_hotkey)
        self.after(2500, self._auto_refresh)

    # ---- UI 构建 ------------------------------------------------------

    def _build_ui(self) -> None:
        # 顶部工具栏
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=6)
        self._top_bar = top
        ttk.Label(top, text="检测到的触摸设备:").pack(side="left")
        ttk.Button(top, text="刷新", width=6, command=self.refresh_devices).pack(
            side="right", padx=(4, 0)
        )
        self.topmost_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            top,
            text="置顶",
            variable=self.topmost_var,
            command=self._apply_topmost,
        ).pack(side="right", padx=6)

        # 设备列表
        self.tree = ttk.Treeview(
            self, columns=("name", "status", "class"), show="headings",
            selectmode="browse",
        )
        self.tree.heading("name", text="设备")
        self.tree.heading("status", text="状态")
        self.tree.heading("class", text="类别")
        self.tree.column("name", width=320, anchor="w")
        self.tree.column("status", width=90, anchor="center")
        self.tree.column("class", width=120, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=8)
        self.tree.bind("<Double-1>", lambda _e: self.toggle_selected())

        # 操作按钮
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=8, pady=4)
        self._btns_frame = btns
        ttk.Button(
            btns, text="全部切换 (%s)" % HOTKEY_TEXT, command=self.toggle_all
        ).pack(side="left", padx=2)
        ttk.Button(btns, text="全部启用", command=lambda: self.set_all(True)).pack(
            side="left", padx=2
        )
        ttk.Button(btns, text="全部禁用", command=lambda: self.set_all(False)).pack(
            side="left", padx=2
        )
        ttk.Button(btns, text="切换选中", command=self.toggle_selected).pack(
            side="left", padx=2
        )
        ttk.Button(btns, text="迷你模式", command=lambda: self._set_mini(True)).pack(
            side="right", padx=2
        )

        # 自启行
        auto = ttk.Frame(self)
        auto.pack(fill="x", padx=8, pady=(0, 4))
        self._auto_frame = auto
        self.autostart_var = tk.StringVar(value="开机自启: 检测中...")
        ttk.Label(auto, textvariable=self.autostart_var).pack(side="left")
        ttk.Button(
            auto, text="设置自启", width=10, command=lambda: self._set_autostart(True)
        ).pack(side="right", padx=2)
        ttk.Button(
            auto, text="取消自启", width=10, command=lambda: self._set_autostart(False)
        ).pack(side="right", padx=2)

        # 迷你模式界面(初始隐藏)
        self.mini_frame = ttk.Frame(self)
        self.mini_state_var = tk.StringVar(value="触摸屏: --")
        self.mini_button = ttk.Button(
            self.mini_frame, textvariable=self.mini_state_var, command=self.toggle_all
        )
        self.mini_button.pack(fill="both", expand=True, padx=16, pady=16)
        mini_btns = ttk.Frame(self.mini_frame)
        mini_btns.pack(fill="x", padx=8)
        ttk.Button(
            mini_btns, text="刷新", width=8, command=self.refresh_devices
        ).pack(side="left")
        ttk.Button(
            mini_btns, text="完整模式", command=lambda: self._set_mini(False)
        ).pack(side="right")

        # 状态栏
        self.status_var = tk.StringVar(value="就绪")
        self._status_bar = ttk.Label(
            self, textvariable=self.status_var, anchor="w", relief="sunken"
        )
        self._status_bar.pack(fill="x", side="bottom")

    def _apply_topmost(self) -> None:
        self.attributes("-topmost", bool(self.topmost_var.get()))

    def _set_mini(self, on: bool) -> None:
        if on == self._mini:
            return
        self._mini = on
        if on:
            self._top_bar.pack_forget()
            self.tree.pack_forget()
            self._btns_frame.pack_forget()
            self._auto_frame.pack_forget()
            self.mini_frame.pack(fill="both", expand=True)
            self.geometry("280x150")
        else:
            self.mini_frame.pack_forget()
            self._top_bar.pack(fill="x", padx=8, pady=6)
            self.tree.pack(fill="both", expand=True, padx=8)
            self._btns_frame.pack(fill="x", padx=8, pady=4)
            self._auto_frame.pack(fill="x", padx=8, pady=(0, 4))
            self.geometry("600x430")
        self._update_mini_label()

    # ---- 设备列表 -----------------------------------------------------

    def refresh_devices(self) -> None:
        selected = self._selected_instance_id()
        try:
            self.devices = list_touch_devices()
        except Exception as exc:
            self.status_var.set("枚举失败: %s" % exc)
            self.devices = []
        self._render_devices(selected)
        self._update_mini_label()

    def _render_devices(self, selected: str | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        self._row_instance = {}
        reselect = None
        for d in self.devices:
            status_text = {"OK": "启用", "Disabled": "禁用"}.get(d["status"], d["status"])
            item = self.tree.insert(
                "", "end",
                values=(d["name"], status_text, d["class"]),
            )
            self._row_instance[item] = d["instance_id"]
            if d["instance_id"] == selected:
                reselect = item
        if reselect:
            self.tree.selection_set(reselect)
        if not self.devices:
            self.status_var.set("未检测到触摸屏设备(笔记本触摸板已被排除)")
        self._update_mini_label()

    def _selected_instance_id(self) -> str | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return self._row_instance.get(sel[0])

    def _update_mini_label(self) -> None:
        total = len(self.devices)
        on = sum(1 for d in self.devices if d["enabled"])
        if total == 0:
            self.mini_state_var.set("未检测到触摸屏")
        else:
            self.mini_state_var.set("触摸屏 %d/%d 开\n点击切换" % (on, total))

    # ---- 操作 ---------------------------------------------------------

    def toggle_all(self) -> None:
        if not self.devices:
            self.status_var.set("未检测到触摸屏设备")
            return
        enable = not any(d["enabled"] for d in self.devices)
        self.set_all(enable)

    def set_all(self, enable: bool) -> None:
        if not self.devices:
            self.status_var.set("未检测到触摸屏设备")
            return
        action = "启用" if enable else "禁用"
        errors = []
        changed = 0
        for d in self.devices:
            if d["enabled"] == enable:
                continue
            ok, msg = set_device_enabled(d["instance_id"], enable)
            if ok:
                changed += 1
            else:
                errors.append("%s: %s" % (d["name"], msg))
        if errors:
            self.status_var.set("部分失败: %s" % "; ".join(errors)[:220])
        else:
            if changed == 0:
                self.status_var.set("所有触摸设备已处于%s状态" % action)
            else:
                self.status_var.set(
                    "已%s %d 个触摸设备 (%s)" % (action, changed, time.strftime("%H:%M:%S"))
                )
        self.refresh_devices()

    def toggle_selected(self) -> None:
        instance_id = self._selected_instance_id()
        if not instance_id:
            self.status_var.set("请先在列表中选择一个设备")
            return
        for d in self.devices:
            if d["instance_id"] == instance_id:
                ok, msg = set_device_enabled(instance_id, not d["enabled"])
                if ok:
                    self.status_var.set(
                        "已%s %s" % ("启用" if not d["enabled"] else "禁用", d["name"])
                    )
                else:
                    self.status_var.set("失败: %s" % msg)
                break
        self.refresh_devices()

    # ---- 自启 ---------------------------------------------------------

    def _refresh_autostart_label(self) -> None:
        try:
            exists = task_exists()
        except Exception:
            exists = False
        self.autostart_var.set(
            "开机自启: %s" % ("已设置(最高权限)" if exists else "未设置")
        )

    def _set_autostart(self, enable: bool) -> None:
        ok, msg = set_autostart(enable)
        if ok:
            self.status_var.set("开机自启已%s" % ("设置" if enable else "取消"))
        else:
            self.status_var.set("自启设置失败: %s" % msg[:200])
            messagebox.showerror(APP_TITLE, "操作失败:\n%s" % msg[:500])
        self._refresh_autostart_label()

    # ---- 热键与定时刷新 ----------------------------------------------

    def _poll_hotkey(self) -> None:
        try:
            user32 = ctypes.windll.user32
            pressed = (
                user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
                and user32.GetAsyncKeyState(VK_MENU) & 0x8000
                and user32.GetAsyncKeyState(VK_T) & 0x8000
            )
        except Exception:
            pressed = False
        if pressed and not self._hotkey_down:
            self._hotkey_down = True
            self.toggle_all()
        elif not pressed:
            self._hotkey_down = False
        self.after(100, self._poll_hotkey)

    def _auto_refresh(self) -> None:
        self.refresh_devices()
        self.after(2500, self._auto_refresh)


def main() -> None:
    if not is_windows():
        ctypes.windll.user32.MessageBoxW(
            0, "本工具仅支持 Windows。", APP_TITLE, 0x30
        )
        return
    if not is_admin():
        elevate()
        return
    try:
        app = TouchToggleApp(start_mini="--mini" in sys.argv)
        app.mainloop()
    except Exception:
        import traceback
        detail = traceback.format_exc()
        print(detail)
        try:
            ctypes.windll.user32.MessageBoxW(
                0, ("程序运行出错:\n%s" % detail)[:1800], APP_TITLE, 0x30
            )
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
