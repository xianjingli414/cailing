# -*- coding: utf-8 -*-
"""
采灵 WiFi 采集管理工具 — Android 原生壳
Kivy WebView + JavaScript 桥接 → 调用 wifi_scanner._scan_android()
所有 UI 由 index.html 承载，此文件仅负责 WebView 容器和桥接。
"""

import json
import os

# Kivy imports (Buildozer 自动提供)
from kivy.app import App
from kivy.clock import Clock
from kivy.uix.widget import Widget

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


# ══════════════════════════════════════════════════════════
# Kivy 应用入口
# ══════════════════════════════════════════════════════════

class CailingApp(App):
    """最小化 Kivy App，仅承载 Android WebView"""

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

            # ── 注册 JS 桥接 ──
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
        except Exception as e:
            print(f"[Cailing] WebView creation failed: {e}")


if __name__ == '__main__':
    CailingApp().run()
