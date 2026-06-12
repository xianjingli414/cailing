# -*- coding: utf-8 -*-
"""
采灵 WiFi 采集管理工具 — Android 原生壳
Kivy WebView + JavaScript 桥接 → 调用 wifi_scanner._scan_android()
所有 UI 由 index.html 承载，此文件仅负责 WebView 容器和桥接。
v3.0: 修复黑屏+闪退 — 三大问题修复
  1. setContentView → addContentView（保留 Kivy GL 渲染表面，避免 SIGSEGV）
  2. @run_on_ui_thread 改为模块级函数（类方法会导致 AttributeError/崩溃）
  3. Java 端 JsBridge 类 + @JavascriptInterface 注解（API 17+ 必须）
  4. Python 端 _BridgeHandler 实现 Java Handler 接口（pyjnius PythonJavaClass）
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
    def run_on_ui_thread(f):
        return f

try:
    from jnius import autoclass, cast, PythonJavaClass, java_method
    JNIUS_AVAILABLE = True
    Logger.info("[Cailing] jnius loaded")
except ImportError:
    Logger.warning("[Cailing] jnius unavailable — desktop mode")


# ══════════════════════════════════════════════════════════
# Python 端 Bridge Handler — 实现 Java 端 JsBridge.Handler 接口
# ══════════════════════════════════════════════════════════

class _BridgeHandler(PythonJavaClass if JNIUS_AVAILABLE else object):
    """
    实现 com.cailing.JsBridge.Handler 接口
    Java 端 JsBridge 的 @JavascriptInterface 方法调用此 Handler 的对应方法
    """
    if JNIUS_AVAILABLE:
        __javainterfaces__ = ['com/cailing/JsBridge$Handler']
        __javacontext__ = 'app'

    @java_method('()Ljava/lang/String;')
    def onScanWifi(self):
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
    def onScanWifiAndSync(self):
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
    def onIsLoggedIn(self):
        try:
            import api_client
            return json.dumps({"logged_in": api_client.is_logged_in(), "user": api_client.get_current_user()})
        except Exception:
            return json.dumps({"logged_in": False, "user": None})

    @java_method('(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;')
    def onLogin(self, username, password):
        try:
            import api_client
            ok, result = api_client.login(username, password)
            return json.dumps({"success": ok, "result": result if ok else None, "error": result if not ok else None})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    @java_method('()Ljava/lang/String;')
    def onLogout(self):
        try:
            import api_client
            api_client.logout()
        except Exception:
            pass
        return json.dumps({"success": True})

    @java_method('()Ljava/lang/String;')
    def onGetServerUrl(self):
        try:
            import api_client
            return json.dumps({"url": api_client.get_server_url()})
        except Exception:
            return json.dumps({"url": ""})

    @java_method('(Ljava/lang/String;)Ljava/lang/String;')
    def onSetServerUrl(self, url):
        try:
            import api_client
            api_client.set_server_url(url)
        except Exception:
            pass
        return json.dumps({"success": True})


# ══════════════════════════════════════════════════════════
# WebView 创建（模块级函数 — 关键！）
# @run_on_ui_thread 不能用于类方法，会导致 AttributeError 或崩溃
# ══════════════════════════════════════════════════════════

# 全局引用，防止对象被 GC 回收
_webview_ref = None
_bridge_ref = None
_handler_ref = None


@run_on_ui_thread
def _create_webview_fn(app_ref):
    """在 UI 线程上创建 WebView 并叠加到 Activity 上"""
    global _webview_ref, _bridge_ref, _handler_ref

    try:
        Logger.info("[Cailing] Creating WebView on UI thread...")

        mActivity = autoclass('org.kivy.android.PythonActivity').mActivity
        WebView = autoclass('android.webkit.WebView')
        LinearLayout = autoclass('android.widget.LinearLayout')
        LayoutParams = autoclass('android.widget.LinearLayout$LayoutParams')

        webview = WebView(mActivity)

        # ── WebView 设置 ──
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

        # ── JS 桥接（Java 端 JsBridge + Python 端 Handler）──
        try:
            JsBridge = autoclass('com.cailing.JsBridge')
            bridge = JsBridge()
            handler = _BridgeHandler()
            bridge.setHandler(handler)
            webview.addJavascriptInterface(bridge, 'NativeBridge')
            _bridge_ref = bridge    # 保持引用
            _handler_ref = handler  # 保持引用
            Logger.info("[Cailing] JS bridge registered (Java+Python)")
        except Exception as e:
            Logger.warning(f"[Cailing] JS bridge failed: {e}")
            # 降级：尝试直接用 PythonJavaClass 做 Bridge（无 @JavascriptInterface，部分设备可能不工作）
            try:
                bridge_py = _BridgeHandler()
                webview.addJavascriptInterface(bridge_py, 'NativeBridge')
                _handler_ref = bridge_py
                Logger.info("[Cailing] JS bridge fallback (PythonJavaClass only)")
            except Exception as e2:
                Logger.error(f"[Cailing] JS bridge fallback also failed: {e2}")

        # ── WebViewClient ──
        WebViewClient = autoclass('android.webkit.WebViewClient')
        webview.setWebViewClient(WebViewClient())

        # ── WebChromeClient（console.log → logcat）──
        try:
            webview.setWebChromeClient(autoclass('android.webkit.WebChromeClient')())
        except Exception:
            pass

        # ── 加载 index.html ──
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

        # ── 使用 addContentView 叠加 WebView（关键！）──
        # setContentView 会替换 Kivy 的 GL 渲染表面，导致 Kivy 崩溃 (SIGSEGV)
        # addContentView 在现有内容之上叠加，Kivy 表面保留
        layout = LinearLayout(mActivity)
        params = LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT)
        layout.addView(webview, params)
        mActivity.addContentView(layout, params)

        _webview_ref = webview  # 保持引用

        Logger.info("[Cailing] WebView setup complete (addContentView)")

    except Exception as e:
        Logger.error(f"[Cailing] WebView FAILED: {e}\n{traceback.format_exc()}")
        try:
            Clock.schedule_once(lambda dt: _show_error_fn(app_ref, str(e)), 0)
        except Exception:
            pass


def _show_error_fn(app_ref, msg):
    """在 Kivy 线程上显示错误信息"""
    try:
        if hasattr(app_ref, '_loading') and app_ref._loading:
            app_ref._loading.text = f'加载失败:\n{msg}'
    except Exception:
        pass


# ══════════════════════════════════════════════════════════
# Kivy 应用
# ══════════════════════════════════════════════════════════

class CailingApp(App):
    """采灵 App — WebView 容器"""

    def build(self):
        Logger.info("[Cailing] === CailingApp starting ===")
        self._root = Widget()

        # 加载中提示
        self._loading = Label(
            text='采灵 加载中...',
            font_size='20sp',
            color=(0.3, 0.3, 0.3, 1),
            halign='center', valign='middle',
        )
        self._root.bind(size=self._loading.setter('size'),
                         pos=self._loading.setter('pos'))
        self._root.add_widget(self._loading)

        # 延迟初始化
        Clock.schedule_once(self._delayed_init, 0.5)
        return self._root

    def _delayed_init(self, dt):
        if not IS_ANDROID or not JNIUS_AVAILABLE:
            self._loading.text = '桌面模式\n请在浏览器中打开 index.html'
            return
        # 调用模块级函数（不是类方法上的 @run_on_ui_thread）
        _create_webview_fn(self)


if __name__ == '__main__':
    CailingApp().run()
