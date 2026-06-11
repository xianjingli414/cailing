# -*- coding: utf-8 -*-
"""
wifi_scanner.py
WiFi 扫描模块 - Windows(netsh) / Android(jnius) 双平台适配
"""

import platform
import subprocess
import re
import time

# ── OUI 厂商前缀表 ─────────────────────────────────────────

OUI_VENDORS = {
    "00:50:56": "VMware",  "00:0C:29": "VMware",
    "00:1A:2B": "Cisco",   "00:1B:63": "Apple",
    "18:65:90": "Apple",   "F8:1E:DF": "Apple",
    "2C:AB:00": "TP-Link", "54:A7:03": "TP-Link",
    "00:1D:0F": "D-Link",  "20:E5:2A": "Netgear",
    "00:18:82": "Huawei",  "EC:8C:A2": "ASUS",
    "28:6C:07": "Xiaomi",  "F8:A4:5F": "Xiaomi",
    "00:90:4C": "Epigram",
}

def get_vendor(bssid: str) -> str:
    if not bssid:
        return "未知"
    prefix = bssid.upper()[:8]
    for k, v in OUI_VENDORS.items():
        if prefix.startswith(k.upper()):
            return v
    return "未知"


def _parse_signal(pct_str: str) -> int:
    try:
        return -100 + int(pct_str.strip().rstrip("%")) // 2
    except Exception:
        return 0


def _channel_from_freq(freq: int) -> int:
    """将频率(MHz)转换为信道号"""
    if 2412 <= freq <= 2484:
        return (freq - 2412) // 5 + 1
    if freq == 2484:
        return 14
    if 4915 <= freq <= 4980:
        return (freq - 4915) // 5 + 183
    if 5035 <= freq <= 5980:
        return (freq - 5035) // 5 + 7
    return 0


# ══════════════════════════════════════════════════════════
# Windows 扫描
# ══════════════════════════════════════════════════════════

def _scan_windows() -> list:
    results = []
    try:
        raw = subprocess.check_output(
            ["netsh", "wlan", "show", "networks", "mode=bssid"],
            encoding="gbk", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=15,
        )
    except Exception:
        return results

    blocks = re.split(r"\n(?=SSID\s+\d+\s*:)", raw)
    for block in blocks:
        if "BSSID" not in block:
            continue
        ssid_m = re.search(r"^SSID\s+\d+\s*:\s*(.+)", block, re.M)
        ssid = ssid_m.group(1).strip() if ssid_m else ""

        bssids   = [m.group(1) for m in re.finditer(r"BSSID\s+\d+\s*:\s*([0-9A-Fa-f:]{17})", block)]
        signals  = [m.group(1) for m in re.finditer(r"信号\s*:\s*(\d+%)", block)]
        if not signals:
            signals = [m.group(1) for m in re.finditer(r"Signal\s*:\s*(\d+%)", block)]
        channels = [m.group(1) for m in re.finditer(r"信道\s*:\s*(\d+)", block)]
        if not channels:
            channels = [m.group(1) for m in re.finditer(r"Channel\s*:\s*(\d+)", block)]
        encrypts = [m.group(1).strip() for m in re.finditer(r"身份验证\s*:\s*(.+)", block)]
        if not encrypts:
            encrypts = [m.group(1).strip() for m in re.finditer(r"Authentication\s*:\s*(.+)", block)]

        for i, bssid in enumerate(bssids):
            sig_str = signals[i] if i < len(signals) else "0%"
            ch_str  = channels[i] if i < len(channels) else "0"
            enc     = encrypts[i] if i < len(encrypts) else "未知"
            try:
                ch = int(ch_str)
            except Exception:
                ch = 0
            band = "5G" if ch > 14 else ("2.4G" if ch > 0 else "2.4G")
            results.append({
                "ssid":         ssid,
                "bssid":        bssid.upper(),
                "signal":       _parse_signal(sig_str),
                "channel":      ch,
                "encrypt_type": enc,
                "band":         band,
                "vendor":       get_vendor(bssid),
            })
    return results


# ══════════════════════════════════════════════════════════
# Android 扫描（通过 jnius 调用 WifiManager）
# ══════════════════════════════════════════════════════════

def _scan_android() -> list:
    results = []
    try:
        from jnius import autoclass, cast

        Context = autoclass("android.content.Context")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = PythonActivity.mActivity

        wifi_service = activity.getSystemService(Context.WIFI_SERVICE)
        WifiManager = autoclass("android.net.wifi.WifiManager")
        wifi_mgr = cast(WifiManager, wifi_service)

        wifi_mgr.startScan()
        time.sleep(1)  # 等待扫描完成
        scan_results = wifi_mgr.getScanResults()

        for r in scan_results:
            freq = r.frequency
            ch = _channel_from_freq(freq)
            band = "5G" if ch > 14 else ("2.4G" if ch > 0 else "2.4G")
            results.append({
                "ssid":         str(r.SSID) if r.SSID else "<Hidden>",
                "bssid":        str(r.BSSID).upper(),
                "signal":        int(r.level),
                "channel":       ch,
                "encrypt_type": "WPA/WPA2" if r.capabilities else "开放",
                "band":          band,
                "vendor":        get_vendor(str(r.BSSID)),
            })
    except Exception as e:
        print(f"[Android WiFi Scan Error] {e}")
    return results


# ══════════════════════════════════════════════════════════
# 统一入口
# ══════════════════════════════════════════════════════════

def scan_wifi() -> list:
    """
    跨平台 WiFi 扫描。
    Windows  → netsh
    Android  → jnius/WifiManager
    """
    system = platform.system()
    if system == "Windows":
        return _scan_windows()
    if system == "Linux":
        # Android 也是 Linux，但 platform.system() 在 Android 上可能返回 "Linux"
        # 尝试导入 jnius 来判断是否在 Android 上
        try:
            import jnius  # noqa: F401
            return _scan_android()
        except ImportError:
            pass
    return []
