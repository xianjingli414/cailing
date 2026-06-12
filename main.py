# -*- coding: utf-8 -*-
"""
采灵 WiFi 采集管理工具 — Android 原生壳
Kivy WebView + JavaScript 桥接 → 调用 wifi_scanner._scan_android()
所有 UI 由 index.html 承载，此文件仅负责 WebView 容器和桥接。
v2.2: 修复黑屏 — 使用 run_on_ui_thread + 正确的生命周期管理
"""

import json
import os
import traceback

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.widget import Widget
from kivy.uix.label import Label
from kivy.logger import Logger

# ── 平台检测 ──
IS_ANDROID = False
JNIUS_AVAILABLE = False

try:
    from android.runnable import run_on_ui_thread
    IS_ANDROID = True
    Logger.info("[Cailing] android.runnable loaded")
except ImportError:
    IS_ANDROID = False
    # 桌面端降级：run_on_ui_thread 变成直接执行
    def run_on_ui_thread(f):
        return f

try:
    from jnius import autoclass, cast, PythonJavaClass, java_method
    JNIUS_AVAILABLE = True
    Logger.info("[Cailing] jnius loaded")
except ImportError:
    Logger.warning("[Cailing] jnius unavailable — desktop mode")


# ══════════════════════════════════════════════════════════
# JavaScript 桥接对象
# ══════════════════════════════════════════════════════════

class _JsBridge(PythonJavaClass if JNIUS_AVAILABLE else object):
    """
    JS 桥接：HTML → Python 原生方法
    Android 上通过 addJavascriptInterface 注册
    """
    if JNIUS_AVAILABLE:
        __javainterfaces__ = ['java/lang/Object']
        __javacontext__ = 'app'

    @java_method('()Ljava/lang/String;')
    def scanWifi(self):
        try:
            from wifi_scanner import _scan_android
            results = _scan_android()
            if not results:
                return json.dumps({"status": "empty", "message": "未扫描到WiFi网络"})
            return json.dumps(results, ensure_ascii=False)
        except Exception as e:
            Logger.error(f"[Cailing] scanWifi: {e}")
            return json.dumps({"status": "error", "message": str(e)})

    @java_method('()Ljava/lang/String;')
    def scanWifiAndSync(self):
        try:
            from wifi_scanner import _scan_android
            results = _scan_android()
            if not results:
                return json.dumps({"status": "empty", "message": "未扫描到WiFi网络"})

            sync_ok, sync_skip = 0, 0
            try:
                import api_client
                if api_client.is_logged_in():
                    sync_ok, sync_skip = api_client.upload_wifi_records(results)
            except Exception as ae:
                Logger.warning(f"[Cailing] sync: {ae}")

            return json.dumps({
                "status": "ok",
                "data": results,
                "sync_ok": sync_ok,
                "sync_skip": sync_skip,
                "synced": sync_ok > 0
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    @java_method('()Ljava/lang/String;')
    def isLoggedIn(self):
        try:
            import api_client
            return json.dumps({"logged_in": api_client.is_logged_in(), "user": api_client.get_current_user()})
        except Exception:
            return json.dumps({"logged_in": False, "user": None})

    @java_method('(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;')
    def login(self, username, password):
        try:
            import api_client
            ok, result = api_client.login(username, password)
            return json.dumps({"success": ok, "result": result if ok else None, "error": result if not ok else None})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    @java_method('()Ljava/lang/String;')
    def logout(self):
        try:
            import api_client
            api_client.logout()
        except Exception:
            pass
        return json.dumps({"success": True})

    @java_method('()Ljava/lang/String;')
    def getServerUrl(self):
        try:
            import api_client
            return json.dumps({"url": api_client.get_server_url()})
        except Exception:
            return json.dumps({"url": ""})

    @java_method('(Ljava/lang/String;)Ljava/lang/String;')
    def setServerUrl(self, url):
        try:
            import api_client
            api_client.set_server_url(url)
        except Exception:
            pass
        return json.dumps({"success": True})


# ══════════════════════════════════════════════════════════
# Kivy 应用
# ══════════════════════════════════════════════════════════

class CailingApp(App):
    """采灵 App — WebView 容器"""

    def build(self):
        Logger.info("[Cailing] === CailingApp starting ===")
        self._root = Widget()

        # 加载中提示（防止黑屏感知）
        self._loading = Label(
            text='采灵 加载中...',
            font_size='20sp',
            color=(0.3, 0.3, 0.3, 1),
            halign='center', valign='middle',
        )
        self._root.bind(size=self._loading.setter('size'),
                         pos=self._loading.setter('pos'))
        self._root.add_widget(self._loading)

        # 延迟创建 WebView
        Clock.schedule_once(self._delayed_init, 0.5)
        return self._root

    def _delayed_init(self, dt):
        if not IS_ANDROID or not JNIUS_AVAILABLE:
            self._loading.text = '桌面模式\n请在浏览器中打开 index.html'
            return
        self._create_webview()

    @run_on_ui_thread
    def _create_webview(self):
        """在 UI 线程上创建 WebView（关键！）"""
        try:
            Logger.info("[Cailing] Creating WebView on UI thread...")

            mActivity = autoclass('org.kivy.android.PythonActivity').mActivity
            WebView = autoclass('android.webkit.WebView')
            webview = WebView(mActivity)

            # 设置
            s = webview.getSettings()
            s.setJavaScriptEnabled(True)
            s.setDomStorageEnabled(True)
            s.setAllowFileAccess(True)
            s.setAllowContentAccess(True)
            s.setAllowFileAccessFromFileURLs(True)
            s.setAllowUniversalAccessFromFileURLs(True)
            s.setDatabaseEnabled(True)
            s.setUseWideViewPort(True)
            s.setLoadWithOverviewMode(True)
            s.setSupportZoom(False)
            s.setBuiltInZoomControls(False)

            webview.setBackgroundColor(0xFFFFFFFF)

            # JS 桥接
            try:
                bridge = _JsBridge()
                webview.addJavascriptInterface(bridge, 'NativeBridge')
                Logger.info("[Cailing] JS bridge registered ✓")
            except Exception as e:
                Logger.warning(f"[Cailing] JS bridge failed: {e}")

            # WebViewClient
            WebViewClient = autoclass('android.webkit.WebViewClient')
            webview.setWebViewClient(WebViewClient())

            # WebChromeClient（让 console.log 输出到 logcat）
            try:
                webview.setWebChromeClient(autoclass('android.webkit.WebChromeClient')())
            except Exception:
                pass

            # 加载 index.html
            app_dir = os.path.dirname(os.path.abspath(__file__))
            html_path = os.path.join(app_dir, 'index.html')

            if os.path.exists(html_path):
                url = 'file://' + html_path
                Logger.info(f"[Cailing] Loading: {url}")
                webview.loadUrl(url)
            else:
                Logger.error(f"[Cailing] index.html NOT FOUND: {html_path}")
                webview.loadData(
                    '<html><body style="padding:20px;font-size:18px;">'
                    '<h2>加载失败</h2><p>index.html 未找到</p></body></html>',
                    'text/html', 'utf-8'
                )

            # 使用 setContentView 替换整个内容（Kivy 官方推荐方式）
            mActivity.setContentView(webview)

            Logger.info("[Cailing] WebView setup complete ✓")

        except Exception as e:
            Logger.error(f"[Cailing] WebView FAILED: {e}\n{traceback.format_exc()}")
            # 在 Kivy 线程更新 UI
            Clock.schedule_once(lambda dt: self._show_error(str(e)), 0)

    def _show_error(self, msg):
        try:
            self._loading.text = f'加载失败:\n{msg}'
        except Exception:
            pass


if __name__ == '__main__':
    CailingApp().run()
