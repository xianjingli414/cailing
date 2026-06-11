# -*- coding: utf-8 -*-
"""
采灵 WiFi 采集管理工具 — Android 原生壳
Kivy WebView + JavaScript 桥接 → 调用 wifi_scanner._scan_android()
所有 UI 由 index.html 承载，此文件仅负责 WebView 容器和桥接。
v2.0: 增加服务端数据库同步，采集数据实时上传到服务器
"""

import json
import os
import threading

# Kivy imports (Buildozer 自动提供)
from kivy.app import App
from kivy.clock import Clock
from kivy.uix.widget import Widget

# 导入服务端API客户端
import api_client

# Jnius imports（仅在 Android 上可用）
try:
    from jnius import autoclass, cast, PythonJavaClass, java_method
    JNIUS_AVAILABLE = True
except ImportError:
    JNIUS_AVAILABLE = False
    print("[Cailing] jnius not available — running in desktop/debug mode")


# ══════════════════════════════════════════════════════════
# JavaScript 桥接对象 — 暴露给 HTML 调用的原生方法
# ══════════════════════════════════════════════════════════

class _JsBridge(PythonJavaClass if JNIUS_AVAILABLE else object):
    """
    在 Android 上继承 PythonJavaClass 注册为 JS 接口。
    桌面端退化为普通对象，供 HTML 端检测。
    v2.0: 增加 scanWifiAndSync（扫描+上传服务器）
    """
    if JNIUS_AVAILABLE:
        __javainterfaces__ = ['java/lang/Object']
        __javacontext__ = 'app'

    @staticmethod
    def scanWifi():
        """供 JavaScript 同步调用，返回 JSON 字符串"""
        try:
            from wifi_scanner import _scan_android
            results = _scan_android()
            if not results:
                return json.dumps({"status": "empty", "message": "未扫描到 WiFi 网络，请确认已开启位置权限"})
            return json.dumps(results, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    @staticmethod
    def scanWifiAndSync():
        """
        v2.0: 扫描WiFi并同步上传到服务器
        供 JavaScript 同步调用，返回 JSON 字符串
        返回格式: {status: "ok", data: [...], sync_ok: N, sync_skip: N}
                 或 {status: "empty"/"error", ...}
        """
        try:
            from wifi_scanner import _scan_android
            results = _scan_android()
            if not results:
                return json.dumps({"status": "empty", "message": "未扫描到 WiFi 网络，请确认已开启位置权限"})

            # 尝试同步到服务器
            sync_ok, sync_skip = 0, 0
            if api_client.is_logged_in():
                sync_ok, sync_skip = api_client.upload_wifi_records(results)

            return json.dumps({
                "status": "ok",
                "data": results,
                "sync_ok": sync_ok,
                "sync_skip": sync_skip,
                "synced": api_client.is_logged_in()
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    @staticmethod
    def isLoggedIn():
        """检查是否已登录服务器"""
        return json.dumps({"logged_in": api_client.is_logged_in(), "user": api_client.get_current_user()})

    @staticmethod
    def login(username, password):
        """
        登录服务器
        username, password 为字符串参数
        """
        try:
            ok, result = api_client.login(username, password)
            return json.dumps({"success": ok, "result": result if ok else None, "error": result if not ok else None})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    @staticmethod
    def logout():
        """登出服务器"""
        api_client.logout()
        return json.dumps({"success": True})

    @staticmethod
    def getServerUrl():
        """获取当前服务器地址"""
        return json.dumps({"url": api_client.get_server_url()})

    @staticmethod
    def setServerUrl(url):
        """设置服务器地址"""
        api_client.set_server_url(url)
        return json.dumps({"success": True})


# ══════════════════════════════════════════════════════════
# Kivy 应用入口
# ══════════════════════════════════════════════════════════

class CailingApp(App):
    """Kivy App，承载 Android WebView + 服务端同步"""

    def build(self):
        Clock.schedule_once(self._create_webview, 0.3)
        return Widget()

    def _create_webview(self, dt):
        if not JNIUS_AVAILABLE:
            print("[Cailing] Desktop mode: WebView not available. Open index.html in browser instead.")
            return

        try:
            # ── 获取当前 Activity ──
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            mActivity = PythonActivity.mActivity

            # ── 创建 WebView ──
            WebViewCls = autoclass('android.webkit.WebView')
            webview = WebViewCls(mActivity)

            # 启用 JS + DOM存储（localStorage）
            settings = webview.getSettings()
            settings.setJavaScriptEnabled(True)
            settings.setDomStorageEnabled(True)
            settings.setAllowFileAccessFromFileURLs(True)
            settings.setAllowUniversalAccessFromFileURLs(True)
            settings.setDatabaseEnabled(True)

            # 视口自适应
            settings.setUseWideViewPort(True)
            settings.setLoadWithOverviewMode(True)
            settings.setSupportZoom(False)
            settings.setBuiltInZoomControls(False)

            # ── 注册 JS 桥接（v2.0 增强版）──
            webview.addJavascriptInterface(_JsBridge(), 'NativeBridge')

            # ── WebView Client ──
            WebViewClient = autoclass('android.webkit.WebViewClient')
            webview.setWebViewClient(WebViewClient())

            # ── 加载 HTML ──
            app_dir = os.path.dirname(os.path.abspath(__file__))
            html_path = os.path.join(app_dir, 'index.html')
            webview.loadUrl('file://' + html_path)

            # ── 添加到 Activity 布局 ──
            FrameLayout = autoclass('android.widget.FrameLayout')
            params = FrameLayout.LayoutParams(-1, -1)  # MATCH_PARENT
            mActivity.addContentView(webview, params)

            print("[Cailing] WebView loaded successfully ✓")
            print(f"[Cailing] Server sync: {api_client.get_server_url()}")
        except Exception as e:
            print(f"[Cailing] WebView creation failed: {e}")


if __name__ == '__main__':
    CailingApp().run()
