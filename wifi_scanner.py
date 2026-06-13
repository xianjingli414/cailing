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

def _scan_android(max_results=10):
    """
    Android WiFi 扫描 — 读取系统已缓存的扫描结果，按信号强度排序取前 N 条。
    
    不主动调用 startScan()（Android 9+ 需要位置权限且位置服务必须开启才能触发扫描），
    而是直接读取 WifiManager.getScanResults() 获取系统最近一次扫描的结果。
    Android 系统会自动在后台周期性扫描 WiFi，所以即使不主动触发，也能获取到数据。
    
    同时还会获取当前连接的 WiFi 信息（只需 ACCESS_WIFI_STATE 权限），
    作为备用数据源。
    """
    results = []
    try:
        from jnius import autoclass, cast

        Context = autoclass("android.content.Context")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = PythonActivity.mActivity

        if not activity:
            print("[Android WiFi Scan] Error: mActivity is null")
            return results

        wifi_service = activity.getSystemService(Context.WIFI_SERVICE)
        WifiManager = autoclass("android.net.wifi.WifiManager")
        wifi_mgr = cast(WifiManager, wifi_service)

        # ── 方法1: 读取系统缓存的扫描结果 ──
        # 不需要主动调用 startScan()，系统后台会自动扫描
        # Android 6.0+ 需要 ACCESS_FINE_LOCATION 权限才能获取 SSID/BSSID
        # 但即使没有位置权限，getScanResults() 仍可能返回部分结果
        try:
            # 尝试触发一次扫描（静默失败即可，不影响读取缓存结果）
            try:
                wifi_mgr.startScan()
            except Exception:
                pass  # 静默忽略，某些 ROM 可能不允许 app 触发扫描

            scan_results = wifi_mgr.getScanResults()
            if scan_results:
                print(f"[Android WiFi Scan] getScanResults returned {len(scan_results)} results")
                seen_bssids = set()
                for r in scan_results:
                    bssid = str(r.BSSID).upper() if r.BSSID else ""
                    if not bssid or bssid in seen_bssids:
                        continue
                    seen_bssids.add(bssid)
                    freq = r.frequency
                    ch = _channel_from_freq(freq)
                    band = "5G" if ch > 14 else ("2.4G" if ch > 0 else "2.4G")
                    ssid = str(r.SSID) if r.SSID else "<Hidden>"
                    # 处理加密类型
                    caps = str(r.capabilities) if r.capabilities else ""
                    if "WPA3" in caps:
                        enc = "WPA3"
                    elif "WPA2" in caps:
                        enc = "WPA2"
                    elif "WPA" in caps:
                        enc = "WPA/WPA2"
                    elif "WEP" in caps:
                        enc = "WEP"
                    else:
                        enc = "开放"
                    results.append({
                        "ssid":         ssid,
                        "bssid":        bssid,
                        "signal":       int(r.level),
                        "channel":      ch,
                        "encrypt_type": enc,
                        "band":         band,
                        "vendor":       get_vendor(bssid),
                    })
                # 按信号强度排序（level 越大信号越强）
                results.sort(key=lambda x: x["signal"], reverse=True)
        except Exception as e:
            print(f"[Android WiFi Scan] getScanResults error: {e}")

        # ── 方法2: 读取当前连接的 WiFi 信息（作为补充）──
        # WifiInfo 只需要 ACCESS_WIFI_STATE 权限，不需要位置权限
        try:
            wifi_info = wifi_mgr.getConnectionInfo()
            if wifi_info:
                conn_bssid = str(wifi_info.getBSSID()).upper() if wifi_info.getBSSID() else ""
                conn_ssid = str(wifi_info.getSSID()) if wifi_info.getSSID() else ""
                # Android 8.0+ 返回的 SSID 可能带引号
                if conn_ssid.startswith('"') and conn_ssid.endswith('"'):
                    conn_ssid = conn_ssid[1:-1]
                # 如果当前连接的 WiFi 不在扫描结果中，补充添加
                if conn_bssid and conn_bssid != "00:00:00:00:00:00":
                    if not any(r["bssid"] == conn_bssid for r in results):
                        conn_rssi = wifi_info.getRssi()
                        conn_freq = wifi_info.getFrequency() if hasattr(wifi_info, 'getFrequency') else 0
                        ch = _channel_from_freq(conn_freq) if conn_freq else 0
                        band = "5G" if ch > 14 else "2.4G"
                        results.append({
                            "ssid":         conn_ssid or "<Connected>",
                            "bssid":        conn_bssid,
                            "signal":       int(conn_rssi),
                            "channel":      ch,
                            "encrypt_type": "WPA2",
                            "band":         band,
                            "vendor":       get_vendor(conn_bssid),
                        })
                        print(f"[Android WiFi Scan] Added connected WiFi: {conn_ssid} ({conn_bssid})")
        except Exception as e:
            print(f"[Android WiFi Scan] getConnectionInfo error: {e}")

        # ── 方法3: 通过 WifiConfiguration 读取已保存的网络（作为额外补充）──
        try:
            # 需要 ACCESS_WIFI_STATE 权限
            configured = wifi_mgr.getConfiguredNetworks()
            if configured:
                for net in configured:
                    try:
                        net_ssid = str(net.SSID) if net.SSID else ""
                        if net_ssid.startswith('"') and net_ssid.endswith('"'):
                            net_ssid = net_ssid[1:-1]
                        if net_ssid and not any(r["ssid"] == net_ssid for r in results):
                            # 已保存但不在扫描结果中的网络
                            results.append({
                                "ssid":         net_ssid,
                                "bssid":        "SAVED_" + net_ssid[:8],
                                "signal":       -90,  # 信号未知，标记弱信号
                                "channel":      0,
                                "encrypt_type": "已保存",
                                "band":         "2.4G",
                                "vendor":       "未知",
                            })
                    except Exception:
                        pass
                print(f"[Android WiFi Scan] getConfiguredNetworks added saved networks")
        except Exception as e:
            # Android 10+ 不再允许获取 configured networks（返回空列表）
            print(f"[Android WiFi Scan] getConfiguredNetworks error (expected on Android 10+): {e}")

        # 取信号最强的前 max_results 条
        results.sort(key=lambda x: x["signal"], reverse=True)
        results = results[:max_results]
        print(f"[Android WiFi Scan] Final results: {len(results)} networks")

    except Exception as e:
        print(f"[Android WiFi Scan Error] {e}")
        import traceback
        traceback.print_exc()
    return results


# ══════════════════════════════════════════════════════════
# 统一入口
# ══════════════════════════════════════════════════════════

def scan_wifi(max_results=10) -> list:
    """
    跨平台 WiFi 扫描。
    Windows  → netsh
    Android  → jnius/WifiManager（读取系统缓存扫描结果）
    
    max_results: Android 模式下返回的最大结果数（按信号强度排序）
    """
    system = platform.system()
    if system == "Windows":
        return _scan_windows()
    if system == "Linux":
        # Android 也是 Linux，但 platform.system() 在 Android 上可能返回 "Linux"
        # 尝试导入 jnius 来判断是否在 Android 上
        try:
            import jnius  # noqa: F401
            return _scan_android(max_results=max_results)
        except ImportError:
            pass
    return []
