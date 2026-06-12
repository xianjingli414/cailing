# -*- coding: utf-8 -*-
"""
采灵 WiFi 采集管理工具 — Android 原生壳 v4.0
极简版：逐步初始化，每一步都有日志和保护
目标：即使出错也不闪退，而是显示错误信息
"""

import json
import os
import traceback

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.widget import Widget
from kivy.uix.label import Label
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.logger import Logger

# ── 平台检测 ──
IS_ANDROID = False

try:
    from android.runnable import run_on_ui_thread
    IS_ANDROID = True
except ImportError:
    def run_on_ui_thread(f):
        return f

# 全局引用
_g = {
    'webview': None,
    'bridge': None,
    'handler': None,
    'activity': None,
}


# ══════════════════════════════════════════════════════════
# Step 1: 获取 mActivity（最基础，出错概率极低）
# ══════════════════════════════════════════════════════════

def _get_activity():
    """获取当前 Activity 引用"""
    if _g['activity'] is not None:
        return _g['activity']
    try:
        from jnius import autoclass
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        _g['activity'] = PythonActivity.mActivity
        Logger.info("[Cailing] Got mActivity")
        return _g['activity']
    except Exception as e:
        Logger.error(f"[Cailing] Failed to get activity: {e}")
        return None


# ══════════════════════════════════════════════════════════
# Step 2: WebView 创建（模块级函数，不用类方法）
# ══════════════════════════════════════════════════════════

@run_on_ui_thread
def _do_create_webview():
    """在 UI 线程上创建 WebView — 模块级函数"""
    Logger.info("[Cailing] _do_create_webview START")

    try:
        from jnius import autoclass
    except ImportError as e:
        Logger.error(f"[Cailing] jnius import failed: {e}")
        Clock.schedule_once(lambda dt: _update_status("错误: jnius不可用"), 0)
        return

    mActivity = _get_activity()
    if not mActivity:
        Clock.schedule_once(lambda dt: _update_status("错误: 无法获取Activity"), 0)
        return

    try:
        # ── 创建 WebView ──
        WebView = autoclass('android.webkit.WebView')
        webview = WebView(mActivity)
        Logger.info("[Cailing] WebView created")

        # ── 设置 ──
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

        # ── WebViewClient ──
        WebViewClient = autoclass('android.webkit.WebViewClient')
        webview.setWebViewClient(WebViewClient())
        Logger.info("[Cailing] WebViewClient set")

        # ── WebChromeClient ──
        try:
            webview.setWebChromeClient(autoclass('android.webkit.WebChromeClient')())
        except Exception:
            pass

        # ── JS 桥接 — 使用 Java 端 JsBridge（如果可用）──
        _setup_bridge(webview, mActivity)

        # ── 加载 HTML ──
        app_dir = os.path.dirname(os.path.abspath(__file__))
        html_path = os.path.join(app_dir, 'index.html')

        if os.path.exists(html_path):
            url = 'file://' + html_path
            Logger.info(f"[Cailing] Loading: {url}")
            webview.loadUrl(url)
        else:
            Logger.error(f"[Cailing] index.html NOT FOUND at {html_path}")
            webview.loadData(
                '<html><body style="padding:20px;font-size:18px;">'
                '<h2>加载失败</h2><p>index.html 未找到</p></body></html>',
                'text/html', 'utf-8'
            )

        # ── 将 WebView 添加到 Activity ──
        # 关键：用 addContentView 叠加，不用 setContentView 替换
        LinearLayout = autoclass('android.widget.LinearLayout')
        LayoutParams = autoclass('android.widget.LinearLayout$LayoutParams')

        layout = LinearLayout(mActivity)
        params = LayoutParams(
            LayoutParams.MATCH_PARENT,
            LayoutParams.MATCH_PARENT
        )
        layout.addView(webview, params)
        mActivity.addContentView(layout, params)
        Logger.info("[Cailing] WebView added via addContentView ✓")

        _g['webview'] = webview

        # 隐藏 Kivy 加载提示
        Clock.schedule_once(lambda dt: _update_status(""), 0)

    except Exception as e:
        err = f"WebView创建失败:\n{e}\n\n{traceback.format_exc()}"
        Logger.error(f"[Cailing] {err}")
        Clock.schedule_once(lambda dt: _update_status(err), 0)


def _setup_bridge(webview, mActivity):
    """设置 JS 桥接 — 尝试 Java Bridge，失败则降级"""
    Logger.info("[Cailing] Setting up JS bridge...")

    # 方案1: 使用 Java 端 JsBridge + Python Handler
    try:
        from jnius import autoclass, PythonJavaClass, java_method

        JsBridge = autoclass('com.cailing.JsBridge')
        bridge = JsBridge()
        Logger.info("[Cailing] Java JsBridge loaded")

        # Python 端 Handler 实现
        handler = _BridgeHandler()
        bridge.setHandler(handler)
        Logger.info("[Cailing] Python Handler created")

        webview.addJavascriptInterface(bridge, 'NativeBridge')
        _g['bridge'] = bridge
        _g['handler'] = handler
        Logger.info("[Cailing] JS bridge registered (Java+Python) ✓")
        return

    except Exception as e:
        Logger.warning(f"[Cailing] Java bridge failed: {e}")

    # 方案2: 降级 — 直接用 PythonPythonClass
    try:
        from jnius import PythonJavaClass, java_method

        class _SimpleBridge(PythonJavaClass):
            __javainterfaces__ = ['java/io/Serializable']
            __javacontext__ = 'app'

            @java_method('()Ljava/lang/String;')
            def scanWifi(self):
                return _call_python('scanWifi')

            @java_method('()Ljava/lang/String;')
            def scanWifiAndSync(self):
                return _call_python('scanWifiAndSync')

            @java_method('()Ljava/lang/String;')
            def isLoggedIn(self):
                return _call_python('isLoggedIn')

            @java_method('(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;')
            def login(self, username, password):
                return _call_python('login', username, password)

            @java_method('()Ljava/lang/String;')
            def logout(self):
                return _call_python('logout')

            @java_method('()Ljava/lang/String;')
            def getServerUrl(self):
                return _call_python('getServerUrl')

            @java_method('(Ljava/lang/String;)Ljava/lang/String;')
            def setServerUrl(self, url):
                return _call_python('setServerUrl', url)

        bridge = _SimpleBridge()
        webview.addJavascriptInterface(bridge, 'NativeBridge')
        _g['bridge'] = bridge
        Logger.info("[Cailing] JS bridge registered (PythonJavaClass fallback) ✓")
        return

    except Exception as e:
        Logger.warning(f"[Cailing] Python bridge also failed: {e}")

    # 方案3: 无 Bridge — WebView 可用但 JS 调原生功能不可用
    Logger.warning("[Cailing] No JS bridge available — native functions disabled")


def _call_python(method, *args):
    """统一 Python 方法调用入口 — 所有延迟导入，错误隔离"""
    try:
        if method == 'scanWifi':
            from wifi_scanner import _scan_android
            results = _scan_android()
            if not results:
                return json.dumps({"status": "empty", "message": "未扫描到WiFi网络"})
            return json.dumps(results, ensure_ascii=False)

        elif method == 'scanWifiAndSync':
            from wifi_scanner import _scan_android
            results = _scan_android()
            sync_ok, sync_skip = 0, 0
            try:
                import api_client
                if api_client.is_logged_in():
                    sync_ok, sync_skip = api_client.upload_wifi_records(results)
            except Exception:
                pass
            return json.dumps({
                "status": "ok",
                "data": results,
                "sync_ok": sync_ok,
                "sync_skip": sync_skip,
                "synced": sync_ok > 0
            }, ensure_ascii=False) if results else json.dumps({"status": "empty", "message": "未扫描到WiFi网络"})

        elif method == 'isLoggedIn':
            import api_client
            return json.dumps({"logged_in": api_client.is_logged_in(), "user": api_client.get_current_user()})

        elif method == 'login':
            import api_client
            ok, result = api_client.login(args[0], args[1])
            return json.dumps({"success": ok, "result": result if ok else None, "error": result if not ok else None})

        elif method == 'logout':
            try:
                import api_client
                api_client.logout()
            except Exception:
                pass
            return json.dumps({"success": True})

        elif method == 'getServerUrl':
            import api_client
            return json.dumps({"url": api_client.get_server_url()})

        elif method == 'setServerUrl':
            import api_client
            api_client.set_server_url(args[0])
            return json.dumps({"success": True})

        return json.dumps({"status": "error", "message": f"未知方法: {method}"})

    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ══════════════════════════════════════════════════════════
# Python Handler — 实现 Java 端 JsBridge.Handler 接口
# ══════════════════════════════════════════════════════════

try:
    from jnius import PythonJavaClass, java_method

    class _BridgeHandler(PythonJavaClass):
        __javainterfaces__ = ['com/cailing/JsBridge$Handler']
        __javacontext__ = 'app'

        @java_method('()Ljava/lang/String;')
        def onScanWifi(self):
            return _call_python('scanWifi')

        @java_method('()Ljava/lang/String;')
        def onScanWifiAndSync(self):
            return _call_python('scanWifiAndSync')

        @java_method('()Ljava/lang/String;')
        def onIsLoggedIn(self):
            return _call_python('isLoggedIn')

        @java_method('(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;')
        def onLogin(self, username, password):
            return _call_python('login', username, password)

        @java_method('()Ljava/lang/String;')
        def onLogout(self):
            return _call_python('logout')

        @java_method('()Ljava/lang/String;')
        def onGetServerUrl(self):
            return _call_python('getServerUrl')

        @java_method('(Ljava/lang/String;)Ljava/lang/String;')
        def onSetServerUrl(self, url):
            return _call_python('setServerUrl', url)

except ImportError:
    # jnius 不可用时，_BridgeHandler 不会被使用
    _BridgeHandler = None


# ══════════════════════════════════════════════════════════
# UI 状态更新（在 Kivy 线程上安全执行）
# ══════════════════════════════════════════════════════════

_app_ref = None

def _update_status(msg):
    """更新加载状态文字"""
    global _app_ref
    if _app_ref and hasattr(_app_ref, '_status_label') and _app_ref._status_label:
        _app_ref._status_label.text = msg


# ══════════════════════════════════════════════════════════
# Kivy 应用
# ══════════════════════════════════════════════════════════

class CailingApp(App):
    def build(self):
        global _app_ref
        _app_ref = self

        Logger.info("[Cailing] === CailingApp starting ===")

        # 使用 BoxLayout 而不是纯 Widget，更可靠
        root = BoxLayout(orientation='vertical')

        # 状态标签 — 显示加载/错误信息
        self._status_label = Label(
            text='采灵 加载中...',
            font_size='18sp',
            color=(0.4, 0.4, 0.4, 1),
            halign='center',
            valign='middle',
            size_hint_y=0.1,
        )
        root.add_widget(self._status_label)

        # 占位区域（WebView 会叠加在上面）
        placeholder = Label(
            text='',
            size_hint_y=0.9,
        )
        root.add_widget(placeholder)

        # 延迟 1 秒创建 WebView（给 Kivy 足够的初始化时间）
        Clock.schedule_once(self._init_step1, 1.0)
        return root

    def _init_step1(self, dt):
        """Step 1: 检测环境"""
        Logger.info(f"[Cailing] IS_ANDROID={IS_ANDROID}")
        _update_status(f"平台检测: Android={'是' if IS_ANDROID else '否'}")

        if not IS_ANDROID:
            _update_status('桌面模式\n请在浏览器中打开 index.html')
            return

        # Step 2: 获取 Activity
        Clock.schedule_once(self._init_step2, 0.5)

    def _init_step2(self, dt):
        """Step 2: 获取 Activity 引用"""
        _update_status('获取Activity...')
        activity = _get_activity()
        if activity:
            Logger.info("[Cailing] Activity OK")
            _update_status('创建WebView...')
        else:
            _update_status('错误: 无法获取Activity')
            return

        # Step 3: 在 UI 线程创建 WebView
        try:
            _do_create_webview()
        except Exception as e:
            Logger.error(f"[Cailing] WebView creation exception: {e}")
            _update_status(f'创建WebView异常:\n{e}')


if __name__ == '__main__':
    CailingApp().run()
