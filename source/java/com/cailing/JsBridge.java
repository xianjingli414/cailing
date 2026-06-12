package com.cailing;

import android.webkit.JavascriptInterface;

/**
 * JsBridge - JavaScript 桥接类
 * Android API 17+ 要求所有暴露给 JS 的方法必须有 @JavascriptInterface 注解
 *
 * 工作原理：
 * JS 调用 → JsBridge Java 方法 → 返回预先由 Python 设置的结果
 * Python 端通过 setHandler 注册回调，每个方法有对应的 handler
 */
public class JsBridge {

    private Handler handler;

    public interface Handler {
        String onScanWifi();
        String onScanWifiAndSync();
        String onIsLoggedIn();
        String onLogin(String username, String password);
        String onLogout();
        String onGetServerUrl();
        String onSetServerUrl(String url);
    }

    public void setHandler(Handler h) {
        this.handler = h;
    }

    @JavascriptInterface
    public String scanWifi() {
        if (handler == null) return "{\"status\":\"error\",\"message\":\"未初始化\"}";
        return handler.onScanWifi();
    }

    @JavascriptInterface
    public String scanWifiAndSync() {
        if (handler == null) return "{\"status\":\"error\",\"message\":\"未初始化\"}";
        return handler.onScanWifiAndSync();
    }

    @JavascriptInterface
    public String isLoggedIn() {
        if (handler == null) return "{\"logged_in\":false,\"user\":null}";
        return handler.onIsLoggedIn();
    }

    @JavascriptInterface
    public String login(String username, String password) {
        if (handler == null) return "{\"success\":false,\"error\":\"未初始化\"}";
        return handler.onLogin(username, password);
    }

    @JavascriptInterface
    public String logout() {
        if (handler == null) return "{\"success\":true}";
        return handler.onLogout();
    }

    @JavascriptInterface
    public String getServerUrl() {
        if (handler == null) return "{\"url\":\"\"}";
        return handler.onGetServerUrl();
    }

    @JavascriptInterface
    public String setServerUrl(String url) {
        if (handler == null) return "{\"success\":true}";
        return handler.onSetServerUrl(url);
    }
}
