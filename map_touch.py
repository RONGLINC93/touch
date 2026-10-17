#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
触摸屏映射工具:把触摸输入从主屏改到副屏 (Windows, 零第三方依赖)

适用场景:
  两台显示器,触摸屏接在副屏上,但手指触摸时鼠标指针却响应在主屏。

原理:
  Windows 需要"标识"哪个触摸输入设备(数字化仪)对应哪台显示器。
  系统自带 MultiDigiMon 向导就是干这个的:它会在每台显示器上全屏提示,
  在触摸屏上触摸一下即完成绑定。
  本工具自动调用该向导,并替你按 Enter 跳过非触摸屏,
  你只需要最后在副屏(触摸屏)上触摸一下。

用法:
  python map_touch.py     # 首次运行弹一次 UAC(向导 MultiDigiMon 需要管理员权限)
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

APP_TITLE = "触摸屏映射到副屏"
CREATE_NO_WINDOW = 0x08000000
VK_RETURN = 0x0D
KEYEVENTF_KEYUP = 0x0002
PS_TIMEOUT = 30


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
            "触摸标识向导(MultiDigiMon)需要管理员权限,已取消启动。",
            APP_TITLE,
            0x30,  # MB_ICONWARNING
        )
    sys.exit(0)


# ---------------------------------------------------------------------------
# PowerShell 辅助
# ---------------------------------------------------------------------------

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


def list_monitors() -> list[dict]:
    """枚举所有显示器(名称/是否主屏/分辨率/在桌面中的位置)。"""
    script = r"""
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.Screen]::AllScreens | ForEach-Object {
        $b = $_.Bounds
        [PSCustomObject]@{
            Device  = $_.DeviceName
            Primary = $_.Primary
            X = $b.X
            Y = $b.Y
            Width  = $b.Width
            Height = $b.Height
        }
    } | ConvertTo-Json -Compress -Depth 3
    """
    try:
        r = _run_ps(script)
    except Exception as exc:
        raise RuntimeError("枚举显示器失败: %s" % exc) from exc
    raw = (r.stdout or "").strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    monitors = []
    for m in data:
        monitors.append(
            {
                "device": m.get("Device", ""),
                "primary": bool(m.get("Primary")),
                "x": int(m.get("X", 0)),
                "y": int(m.get("Y", 0)),
                "width": int(m.get("Width", 0)),
                "height": int(m.get("Height", 0)),
            }
        )
    return monitors


def list_touch_devices() -> list[dict]:
    """枚举触摸输入设备。

    匹配条件(满足其一):
      - FriendlyName 含 touch/digitizer/触摸/触控/手写
      - 硬件 ID 含 HID_DEVICE_UP:000D_U:0004 (HID Touch Screen 用法页,
        可识别名字不含 touch 的 USB 触摸控制器,如"USB 输入设备")
    排除:触摸板(pad/触摸板/触控板)、鼠标、键盘。
    """
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


# ---------------------------------------------------------------------------
# Windows 输入/窗口辅助
# ---------------------------------------------------------------------------

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def press_enter() -> None:
    user32.keybd_event(VK_RETURN, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)


def _find_window_for_pid(pid: int):
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum_cb(hwnd, _lparam):
        pid_out = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_out))
        if pid_out.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(_enum_cb, 0)
    return found[0] if found else None


def wait_for_window(pid: int, timeout: float = 8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = _find_window_for_pid(pid)
        if hwnd:
            return hwnd
        time.sleep(0.3)
    return None


def focus_window(hwnd) -> None:
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    cur_thread = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(fg_thread, cur_thread, True)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.AttachThreadInput(fg_thread, cur_thread, False)


# ---------------------------------------------------------------------------
# 映射向导
# ---------------------------------------------------------------------------

def run_mapping_wizard(monitor_count: int) -> str:
    """启动系统触摸标识向导,自动跳过非触摸屏,返回给用户的提示信息。"""
    exe = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32", "MultiDigiMon.exe"
    )
    if not os.path.isfile(exe):
        raise FileNotFoundError("找不到 %s" % exe)

    proc = subprocess.Popen([exe, "-touch"])
    hwnd = wait_for_window(proc.pid, timeout=8.0)
    if hwnd is None:
        return (
            "向导窗口没有出现(可能未检测到触摸硬件,或向导已被关闭)。\n"
            "请改用'平板电脑设置'手动完成映射。"
        )
    focus_window(hwnd)
    time.sleep(1.0)

    skips = max(monitor_count - 1, 0)
    for _ in range(skips):
        press_enter()
        time.sleep(1.2)
    if skips:
        return "已自动跳过 %d 个非触摸屏。现在请到副屏(触摸屏)上完成触摸标识。" % skips
    return "现在请直接触摸你的触摸屏完成标识。"


def open_tablet_pc_settings() -> None:
    subprocess.run(
        ["rundll32.exe", "shell32.dll,Control_RunDLL", "tabletpc.cpl"],
        creationflags=CREATE_NO_WINDOW,
    )


def open_display_settings() -> None:
    os.startfile("ms-settings:display")


# ---------------------------------------------------------------------------
# 标记触摸屏:每块屏幕一个全屏覆盖层,触摸哪块就点亮哪块
# ---------------------------------------------------------------------------

class MarkOverlays:
    """在所有显示器上铺全屏大编号;触摸/点击某块屏幕即点亮它并记录顺序。

    用途:直接看出“当前触摸映射到哪台显示器”,以及每块屏的物理编号。
    Esc 或所有屏幕都被触摸后自动结束。
    """

    BG_WAIT = "#1b2838"
    BG_HIT = "#0b6b3a"
    FG = "#ffffff"

    def __init__(self, app: "MapTouchApp", monitors: list[dict]) -> None:
        self.app = app
        self.monitors = monitors
        self.hits: dict[int, str] = {}
        self.windows: list[tk.Toplevel] = []
        self.labels: list[tk.Label] = []
        for idx, m in enumerate(monitors):
            self._make_overlay(idx, m)

    def _make_overlay(self, idx: int, m: dict) -> None:
        win = tk.Toplevel(self.app)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.geometry("%dx%d+%d+%d" % (m["width"], m["height"], m["x"], m["y"]))
        win.configure(bg=self.BG_WAIT)
        win.bind("<Escape>", lambda _e: self.finish())
        win.protocol("WM_DELETE_WINDOW", self.finish)

        role = "主屏" if m["primary"] else "副屏"
        label = tk.Label(
            win,
            text="屏幕 %d\n\n%s\n%s\n\n请用手指触摸这块屏幕" % (idx + 1, role, m["device"]),
            bg=self.BG_WAIT,
            fg=self.FG,
            font=("Microsoft YaHei UI", 42, "bold"),
            justify="center",
        )
        label.pack(fill="both", expand=True)
        label.bind("<Button-1>", lambda _e, i=idx: self.hit(i))
        win.bind("<Button-1>", lambda _e, i=idx: self.hit(i))
        self.windows.append(win)
        self.labels.append(label)

    def hit(self, idx: int) -> None:
        if idx in self.hits:
            return
        self.hits[idx] = time.strftime("%H:%M:%S")
        win = self.windows[idx]
        label = self.labels[idx]
        role = "主屏" if self.monitors[idx]["primary"] else "副屏"
        win.configure(bg=self.BG_HIT)
        label.configure(
            bg=self.BG_HIT,
            text="屏幕 %d\n\n✓ 触摸命中\n%s\n%s" % (idx + 1, role, self.hits[idx]),
        )
        if len(self.hits) >= len(self.monitors):
            self.app.after(600, self.finish)

    def finish(self) -> None:
        for win in self.windows:
            try:
                win.destroy()
            except Exception:
                pass
        self.app.on_marking_finished(dict(self.hits))


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

INTRO = (
    "多屏触摸错位(触摸副屏、光标却动在主屏)的原因是:Windows 还不知道\n"
    "哪个触摸输入设备对应哪台显示器。\n\n"
    "解决办法:\n"
    "  1. 点“标记触摸屏”,每块屏幕会出现全屏大编号,用手指触摸你的触摸屏,\n"
    "     点亮的屏幕就是当前触摸映射的目标(错位时点亮的是主屏);\n"
    "  2. 点“开始映射向导(推荐)”,工具自动帮你在非触摸屏上按 Enter 跳过,\n"
    "     提示出现在副屏(触摸屏)上时,用手指触摸它,完成绑定。\n\n"
    "如果做完映射后触摸有偏移(点左边反应到右边),再打开“平板电脑设置”\n"
    "点“校准”。另外:两台显示器的排列位置必须和实际摆放一致,否则先到\n"
    "“显示设置”里调整;显示模式必须是“扩展”,不能用“复制”。"
)


class MapTouchApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("680x600")
        self.minsize(600, 520)
        self.monitors: list[dict] = []
        self.devices: list[dict] = []
        self.mark_hits: dict[int, str] = {}  # 显示器序号 -> 触摸命中时间

        self._build_ui()
        self.refresh()

    # ---- UI ----

    def _build_ui(self) -> None:
        text = tk.Text(self, height=12, wrap="word", relief="flat", bg="#f5f5f5")
        text.insert("1.0", INTRO)
        text.configure(state="disabled")
        text.pack(fill="x", padx=10, pady=(10, 6))

        # 显示器
        ttk.Label(self, text="显示器:").pack(anchor="w", padx=10)
        self.mon_tree = ttk.Treeview(
            self, columns=("role", "res", "pos", "touch"), show="headings", height=4
        )
        self.mon_tree.heading("role", text="主/副屏")
        self.mon_tree.heading("res", text="分辨率")
        self.mon_tree.heading("pos", text="桌面位置(左上角)")
        self.mon_tree.heading("touch", text="触摸命中")
        self.mon_tree.column("#0", width=0, stretch=False)
        self.mon_tree.column("role", width=80, anchor="center")
        self.mon_tree.column("res", width=110, anchor="center")
        self.mon_tree.column("pos", width=150, anchor="center")
        self.mon_tree.column("touch", width=90, anchor="center")
        self.mon_tree.pack(fill="x", padx=10)

        # 触摸设备
        ttk.Label(self, text="检测到的触摸输入设备:").pack(anchor="w", padx=10, pady=(8, 0))
        self.dev_tree = ttk.Treeview(
            self, columns=("status", "class"), show="headings", height=3
        )
        self.dev_tree.heading("status", text="状态")
        self.dev_tree.heading("class", text="类别")
        self.dev_tree.column("#0", width=0, stretch=False)
        self.dev_tree.column("status", width=90, anchor="center")
        self.dev_tree.column("class", width=120, anchor="center")
        self.dev_tree.pack(fill="x", padx=10)

        # 按钮
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=10, pady=12)
        ttk.Button(
            btns, text="开始映射向导(推荐)", command=self.start_wizard
        ).pack(side="left", padx=2)
        ttk.Button(
            btns, text="平板电脑设置", command=lambda: open_tablet_pc_settings()
        ).pack(side="left", padx=2)
        ttk.Button(
            btns, text="标记触摸屏", command=self.start_marking
        ).pack(side="left", padx=2)
        ttk.Button(
            btns, text="显示设置", command=lambda: open_display_settings()
        ).pack(side="left", padx=2)
        ttk.Button(btns, text="刷新", command=self.refresh).pack(side="right", padx=2)

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self, textvariable=self.status_var, anchor="w", relief="sunken").pack(
            fill="x", side="bottom"
        )

    # ---- 逻辑 ----

    def refresh(self) -> None:
        try:
            self.monitors = list_monitors()
        except Exception as exc:
            self.monitors = []
            self.status_var.set("枚举显示器失败: %s" % exc)
        try:
            self.devices = list_touch_devices()
        except Exception as exc:
            self.devices = []
            self.status_var.set("枚举设备失败: %s" % exc)
        self._render_monitors()
        self._render_devices()

    def _render_monitors(self) -> None:
        self.mon_tree.delete(*self.mon_tree.get_children())
        for idx, m in enumerate(self.monitors):
            hit = self.mark_hits.get(idx)
            self.mon_tree.insert(
                "",
                "end",
                text=m["device"],
                values=(
                    "主屏" if m["primary"] else "副屏",
                    "%d x %d" % (m["width"], m["height"]),
                    "(%d, %d)" % (m["x"], m["y"]),
                    ("✓ 命中 %s" % hit) if hit else ("未命中" if self.mark_hits else "-"),
                ),
            )

    def _render_devices(self) -> None:
        self.dev_tree.delete(*self.dev_tree.get_children())
        for d in self.devices:
            status_text = {"OK": "启用", "Disabled": "禁用"}.get(d["status"], d["status"])
            self.dev_tree.insert(
                "", "end", text=d["name"],
                values=(status_text, d["class"]),
            )
        if not self.devices:
            self.status_var.set("未检测到触摸输入设备(如果触摸屏未插好,插上后点刷新)")

    def start_wizard(self) -> None:
        if not self.monitors:
            messagebox.showwarning(APP_TITLE, "没有检测到显示器,请点“刷新”重试。")
            return
        if len(self.monitors) < 2 and not messagebox.askyesno(
            APP_TITLE, "只检测到 1 台显示器,仍要继续映射向导吗?"
        ):
            return
        if not messagebox.askokcancel(
            APP_TITLE,
            "即将启动触摸标识向导:\n\n"
            "1. 屏幕会全屏变蓝,提示“按 Enter 移到下一屏幕”;\n"
            "2. 本工具会自动帮你在非触摸屏上按 Enter 跳过;\n"
            "3. 看到提示出现在副屏(触摸屏)上时,用手指触摸它。\n\n"
            "点击“确定”开始。",
        ):
            return

        # 最小化主窗口,避免挡住副屏上的触摸提示
        self.iconify()
        self.update()
        try:
            msg = run_mapping_wizard(len(self.monitors))
        except Exception as exc:
            self.deiconify()
            messagebox.showerror(APP_TITLE, "启动向导失败:\n%s" % exc)
            self.status_var.set("向导启动失败")
            return
        messagebox.showinfo(APP_TITLE, msg + "\n\n完成后触摸就会响应副屏了。")
        self.deiconify()
        self.status_var.set("映射向导已执行,如触摸仍错位请重试或改用'平板电脑设置'")

    # ---- 标记触摸屏 ----

    def start_marking(self) -> None:
        """每块屏幕全屏显示大编号,触摸哪块就点亮哪块,直观看当前映射到哪。"""
        if not self.monitors:
            messagebox.showwarning(APP_TITLE, "没有检测到显示器,请点“刷新”重试。")
            return
        self.mark_hits = {}
        self._render_monitors()
        self.iconify()
        self.update()
        self._marker = MarkOverlays(self, self.monitors)
        self.status_var.set("标记中:请用手指触摸每一块屏幕,按 Esc 结束")

    def on_marking_finished(self, hits: dict) -> None:
        self.deiconify()
        self.mark_hits = hits
        self._render_monitors()
        if not hits:
            self.status_var.set("标记已取消,没有记录到触摸")
            return
        order = sorted(hits.items(), key=lambda kv: kv[1])
        names = []
        for idx, _t in order:
            m = self.monitors[idx]
            names.append("屏幕%d(%s)" % (idx + 1, "主屏" if m["primary"] else "副屏"))
        first = self.monitors[order[0][0]]
        first_name = "屏幕%d(%s)" % (order[0][0] + 1, "主屏" if first["primary"] else "副屏")
        self.status_var.set("触摸首先命中: %s" % first_name)
        messagebox.showinfo(
            APP_TITLE,
            "触摸命中顺序: %s\n\n"
            "你刚触摸的是物理触摸屏,而点亮的是【%s】。\n"
            "如果它不是你的触摸屏,说明当前触摸映射错误,\n"
            "请点击“开始映射向导(推荐)”修复。" % (" → ".join(names), first_name),
        )


def set_dpi_awareness() -> None:
    """让全屏覆盖层在混合 DPI 多屏下也能精确对位(须在创建窗口前调用)。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE_V2
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def main() -> None:
    if not is_windows():
        ctypes.windll.user32.MessageBoxW(
            0, "本工具仅支持 Windows。", APP_TITLE, 0x30
        )
        return
    set_dpi_awareness()
    if not is_admin():
        elevate()
        return
    app = MapTouchApp()
    app.mainloop()


if __name__ == "__main__":
    main()
