# -*- coding: utf-8 -*-
"""临时诊断:对比不同设备的 Disable-PnpDevice 错误详情"""
import subprocess

CREATE_NO_WINDOW = 0x08000000

TOUCH = "HID\\VID_1D50&PID_8572\\6&16F56F96&0&0000"
GNA = "PCI\\VEN_8086&DEV_A74F&SUBSYS_7E061462&REV_01\\3&11583659&0&40"
APPLE_HID = "HID\\VID_05AC&PID_0202&MI_01&COL01\\9&50B72C1&0&0000"


def try_disable(instance_id: str, label: str) -> None:
    script = (
        "$id = '%s'; "
        "try { Disable-PnpDevice -InstanceId $id -Confirm:$false -ErrorAction Stop; 'RESULT:OK' } "
        "catch { "
        "  $e = $_.Exception; "
        "  $native = ''; "
        "  if ($e -is [System.ComponentModel.Win32Exception]) { $native = ' native=' + $e.NativeErrorCode }; "
        "  'RESULT:ERR msg=' + $e.Message + ' type=' + $e.GetType().Name + $native + "
        "  ' fqeid=' + $_.FullyQualifiedErrorId + ' cat=' + $_.CategoryInfo.Category "
        "}"
        % instance_id
    )
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NoLogo", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=60,
        creationflags=CREATE_NO_WINDOW,
    )
    print("== %s ==" % label)
    print((r.stdout or "").strip())
    if r.stderr.strip():
        print("STDERR:", r.stderr.strip())
    print()


try_disable(TOUCH, "touch device (non-admin)")
try_disable(GNA, "Intel GNA (non-admin)")
try_disable(APPLE_HID, "Apple keyboard HID child (non-admin)")
