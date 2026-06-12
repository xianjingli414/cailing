# -*- coding: utf-8 -*-
"""
采灵 v9.0 — addContentView + 抑制 Kivy GL 渲染

核心策略（基于 Android-for-Python/Webview-Example 项目的验证方案）：
  1. Kivy App.run() 正常启动，维持 Android 生命周期
  2. addContentView 将 WebView 叠加在 Kivy SurfaceView 上层
  3. 【关键】Monkey-patch EventLoop.idle，跳过 on_draw/on_flip
     → Kivy 不再向 GL SurfaceView 发送渲染命令
     → 消除 GL 渲染与 WebView 的冲突
  4. 隐藏 SDL SurfaceView（setVisibility INVISIBLE），彻底消除 GL 层
  5. WebView 成为用户唯一可见的 UI

之前版本失败的原因分析：
  v7.0/v8.0：addContentView + setLayerType(SOFTWARE) → 黑屏
    原因：Kivy 仍在渲染 GL，两个渲染层冲突
  v7.1：setContentView → 黑屏
    原因：Kivy 仍在向已移除的 SurfaceView 渲染，导致 EGL 死锁
  v9.0：addContentView + 抑制 Kivy GL + 隐藏 Surface → 应该能解决
"""

import json
import os
import traceback

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.widget import Widget
from kivy.logger import Logger
from kivy.base import EventLoop

IS_ANDROID = False
try:
    from android.runnable import run_on_ui_thread
    IS_ANDROID = True
except ImportError:
    def run_on_ui_thread(f):
        return f


# ══════════════════════════════════════════════════════════
#  JS Bridge 处理函数
# ══════════════════════════════════════════════════════════

def _scan_wifi():
    try:
        from wifi_scanner import _scan_android
        r = _scan_android()
        if not r:
            return json.dumps({"status": "empty", "message": "未扫描到WiFi"}, ensure_ascii=False)
        return json.dumps(r, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)


def _scan_wifi_and_sync():
    try:
        from wifi_scanner import _scan_android
        import api_client
        r = _scan_android()
        if not r:
            return json.dumps({"status": "empty", "message": "未扫描到WiFi"}, ensure_ascii=False)
        sync_ok = sync_skip = 0
        synced = False
        if api_client.is_logged_in():
            try:
                sync_ok, sync_skip = api_client.upload_wifi_records(r)
                synced = True
            except Exception:
                pass
        return json.dumps({
            "data": r, "synced": synced,
            "sync_ok": sync_ok, "sync_skip": sync_skip
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)


def _login(u, p):
    try:
        import api_client
        ok, data = api_client.login(u, p)
        if ok:
            return json.dumps({"success": True, "result": data}, ensure_ascii=False)
        else:
            return json.dumps({"success": False, "error": str(data)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


def _logout():
    try:
        import api_client
        api_client.logout()
    except Exception:
        pass
    return json.dumps({"success": True}, ensure_ascii=False)


def _is_logged_in():
    try:
        import api_client
        logged = api_client.is_logged_in()
        user = api_client.get_current_user()
        return json.dumps({"logged_in": logged, "user": user}, ensure_ascii=False)
    except Exception:
        return json.dumps({"logged_in": False, "user": None}, ensure_ascii=False)


def _get_server_url():
    try:
        import api_client
        return json.dumps({"url": api_client.get_server_url()}, ensure_ascii=False)
    except Exception:
        return json.dumps({"url": "http://121.4.28.216:8080"}, ensure_ascii=False)


def _set_server_url(url):
    try:
        import api_client
        api_client.set_server_url(url)
        return json.dumps({"success": True}, ensure_ascii=False)
    except Exception:
        return json.dumps({"success": False}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════
#  Kivy GL 渲染抑制
#  Monkey-patch EventLoop.idle，跳过 on_draw/on_flip
#  这样 Kivy 不会向 GL SurfaceView 发送渲染命令
# ══════════════════════════════════════════════════════════

_original_idle = None


def _pause_kivy_gl():
    """抑制 Kivy GL 渲染：跳过 on_draw 和 on_flip"""
    global _original_idle
    if _original_idle is not None:
        return  # 已经抑制了

    _original_idle = EventLoop.idle

    def idle_no_draw(self_el):
        """替代 EventLoop.idle，跳过 GL 绘制"""
        from kivy.clock import Clock
        Clock.tick()
        if not self_el.quit:
            self_el.dispatch_input()
        # 跳过 on_draw 和 on_flip — 不渲染 GL
        # Clock.tick_draw() 也跳过 — 不需要处理 Kivy widget 动画

    EventLoop.idle = idle_no_draw
    Logger.info('[Cailing] Kivy GL rendering PAUSED')


def _resume_kivy_gl():
    """恢复 Kivy GL 渲染"""
    global _original_idle
    if _original_idle is not None:
        EventLoop.idle = _original_idle
        _original_idle = None
        Logger.info('[Cailing] Kivy GL rendering RESUMED')


# ══════════════════════════════════════════════════════════
#  权限请求
# ══════════════════════════════════════════════════════════

def _request_permissions():
    if not IS_ANDROID:
        return
    try:
        from jnius import autoclass
        activity = autoclass('org.kivy.android.PythonActivity').mActivity
        if hasattr(activity, 'requestPermissions'):
            activity.requestPermissions([
                'android.permission.ACCESS_FINE_LOCATION',
                'android.permission.ACCESS_COARSE_LOCATION',
                'android.permission.ACCESS_WIFI_STATE',
                'android.permission.CHANGE_WIFI_STATE',
            ], 0)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════
#  WebView 创建
# ══════════════════════════════════════════════════════════

_webview_ref = None
_layout_ref = None
_bridge_ref = None
_jsbridge_ref = None


@run_on_ui_thread
def _hide_kivy_surface():
    """隐藏 Kivy 的 SDL SurfaceView，消除 GL 层"""
    try:
        from jnius import autoclass, cast
        mActivity = autoclass('org.kivy.android.PythonActivity').mActivity
        View = autoclass('android.view.View')
        ViewGroup = autoclass('android.view.ViewGroup')

        # 获取 Kivy 的 SurfaceView 并隐藏
        # SDLActivity 的 surface 存储在 mSurface 字段
        try:
            SDLActivity = autoclass('org.libsdl.app.SDLActivity')
            surface = SDLActivity.mSurface
            if surface:
                surface.setVisibility(View.INVISIBLE)
                Logger.info('[Cailing] SDL SurfaceView set to INVISIBLE')
        except Exception:
            pass

        # 备选：遍历 Activity 的 view hierarchy 找到 SurfaceView 并隐藏
        try:
            content = mActivity.findViewById(0x01020002)  # android.R.id.content
            if content and isinstance(content, ViewGroup):
                for i in range(content.getChildCount()):
                    child = content.getChildAt(i)
                    class_name = child.getClass().getName()
                    if 'SurfaceView' in class_name or 'SDL' in class_name:
                        child.setVisibility(View.INVISIBLE)
                        Logger.info('[Cailing] Found and hid: ' + class_name)
        except Exception as e:
            Logger.warning('[Cailing] Could not traverse view hierarchy: ' + str(e))

    except Exception as e:
        Logger.warning('[Cailing] _hide_kivy_surface failed: ' + str(e))


@run_on_ui_thread
def _create_webview():
    """
    创建 WebView 并叠加在 Activity 上。
    使用 addContentView 叠加（而不是 setContentView 替换），
    因为 addContentView 保持 SDL SurfaceView 在 view hierarchy 中，
    不会破坏 SDL 的 surface 引用。
    同时已经通过 _pause_kivy_gl() 抑制了 GL 渲染，
    通过 _hide_kivy_surface() 隐藏了 SDL surface。
    """
    global _webview_ref, _layout_ref, _bridge_ref, _jsbridge_ref

    Logger.info('[Cailing] v9.0 _create_webview START')

    try:
        from jnius import autoclass, PythonJavaClass, java_method

        mActivity = autoclass('org.kivy.android.PythonActivity').mActivity

        # ── 1. 创建 WebView ──
        WebView = autoclass('android.webkit.WebView')
        webview = WebView(mActivity)
        Logger.info('[Cailing] WebView created')

        # ── 2. Settings ──
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
        s.setMediaDefaultPlaybackRequiresUserGesture(False)
        webview.setBackgroundColor(0xFFFFFFFF)  # 白色背景
        Logger.info('[Cailing] WebView Settings done')

        # ── 3. WebViewClient ──
        WebViewClient = autoclass('android.webkit.WebViewClient')
        webview.setWebViewClient(WebViewClient())

        # ── 4. WebChromeClient（处理 JS console/alert）──
        try:
            WebChromeClient = autoclass('android.webkit.WebChromeClient')
            webview.setWebChromeClient(WebChromeClient())
        except Exception:
            pass
        Logger.info('[Cailing] WebViewClient + ChromeClient set')

        # ── 5. JS Bridge ──
        bridge_ok = False

        # 方案A：Java JsBridge + Python Handler（推荐）
        try:
            JsBridge = autoclass('com.cailing.JsBridge')
            jsbridge = JsBridge()
            Logger.info('[Cailing] Java JsBridge loaded')

            class PyHandler(PythonJavaClass):
                __javainterfaces__ = ['com/cailing/JsBridge$Handler']

                @java_method('()Ljava/lang/String;')
                def onScanWifi(self):
                    return _scan_wifi()

                @java_method('()Ljava/lang/String;')
                def onScanWifiAndSync(self):
                    return _scan_wifi_and_sync()

                @java_method('(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;')
                def onLogin(self, u, p):
                    return _login(u, p)

                @java_method('()Ljava/lang/String;')
                def onLogout(self):
                    return _logout()

                @java_method('()Ljava/lang/String;')
                def onIsLoggedIn(self):
                    return _is_logged_in()

                @java_method('()Ljava/lang/String;')
                def onGetServerUrl(self):
                    return _get_server_url()

                @java_method('(Ljava/lang/String;)Ljava/lang/String;')
                def onSetServerUrl(self, url):
                    return _set_server_url(url)

            handler = PyHandler()
            jsbridge.setHandler(handler)
            webview.addJavascriptInterface(jsbridge, 'NativeBridge')
            _bridge_ref = handler
            _jsbridge_ref = jsbridge
            bridge_ok = True
            Logger.info('[Cailing] JsBridge + PyHandler OK')

        except Exception as e:
            Logger.warning('[Cailing] JsBridge+PyHandler failed: ' + str(e))

        # 方案B：PythonJavaClass 直接注册（降级）
        if not bridge_ok:
            try:
                class SimpleBridge(PythonJavaClass):
                    __javainterfaces__ = ['java/io/Serializable']

                    @java_method('()Ljava/lang/String;')
                    def scanWifi(self):
                        return _scan_wifi()

                    @java_method('()Ljava/lang/String;')
                    def scanWifiAndSync(self):
                        return _scan_wifi_and_sync()

                    @java_method('(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;')
                    def login(self, u, p):
                        return _login(u, p)

                    @java_method('()Ljava/lang/String;')
                    def logout(self):
                        return _logout()

                    @java_method('()Ljava/lang/String;')
                    def isLoggedIn(self):
                        return _is_logged_in()

                bridge = SimpleBridge()
                webview.addJavascriptInterface(bridge, 'NativeBridge')
                _bridge_ref = bridge
                bridge_ok = True
                Logger.info('[Cailing] SimpleBridge fallback OK')

            except Exception as e2:
                Logger.error('[Cailing] All bridges failed: ' + str(e2))

        # ── 6. 加载 HTML ──
        app_dir = os.path.dirname(os.path.abspath(__file__))
        html_path = os.path.join(app_dir, 'index.html')
        if os.path.exists(html_path):
            webview.loadUrl('file://' + html_path)
            Logger.info('[Cailing] loading: file://' + html_path)
        else:
            webview.loadUrl('http://121.4.28.216:8080/')
            Logger.info('[Cailing] loading: http://121.4.28.216:8080/')

        # ── 7. 用 LinearLayout 包裹 WebView（推荐方式）──
        LinearLayout = autoclass('android.widget.LinearLayout')
        LayoutParams = autoclass('android.view.ViewGroup$LayoutParams')

        layout = LinearLayout(mActivity)
        layout.setOrientation(LinearLayout.VERTICAL)
        layout.addView(webview, LayoutParams(-1, -1))

        # ── 8. addContentView 叠加 WebView ──
        mActivity.addContentView(layout, LayoutParams(-1, -1))
        Logger.info('[Cailing] addContentView(WebView layout) OK')

        _webview_ref = webview
        _layout_ref = layout

    except Exception as e:
        Logger.error('[Cailing] FAILED: ' + str(e))
        Logger.error(traceback.format_exc())


# ══════════════════════════════════════════════════════════
#  App 类
# ══════════════════════════════════════════════════════════

class CailingApp(App):
    def build(self):
        Logger.info('[Cailing] === v9.0 === build()')
        return Widget()  # 空 Widget，不可见

    def on_start(self):
        """Kivy 启动完成后回调"""
        Logger.info('[Cailing] on_start — Kivy fully initialized')

        if not IS_ANDROID:
            Logger.info('[Cailing] Not Android, skipping WebView')
            return

        # 步骤 1：请求权限
        Clock.schedule_once(lambda dt: _request_permissions(), 0.1)

        # 步骤 2：抑制 Kivy GL 渲染（必须在 addContentView 之前）
        Clock.schedule_once(lambda dt: _pause_kivy_gl(), 0.2)

        # 步骤 3：隐藏 SDL SurfaceView
        Clock.schedule_once(lambda dt: _hide_kivy_surface(), 0.3)

        # 步骤 4：创建并叠加 WebView
        Clock.schedule_once(lambda dt: _create_webview(), 0.5)

        Logger.info('[Cailing] WebView creation scheduled')


# ══════════════════════════════════════════════════════════
#  启动
# ══════════════════════════════════════════════════════════

if __name__ == '__main__':
    CailingApp().run()
