[app]
title = 采灵
package.name = cailing
package.domain = com.cailing

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,html,db,json

version = 1.0.0

# ── 关键：使用 webview bootstrap，彻底告别 SDL2/GL ──
# 不再需要 kivy！Flask 替代 Kivy 做 UI 后端
requirements = python3,flask,pyjnius

# webview bootstrap（不再用 SDL2，不再有 GL/SurfaceView 冲突）
p4a.bootstrap = webview
p4a.port = 5000

orientation = portrait
fullscreen = 0
allow_screenshots = 1

# 自动接受 Android SDK 许可证
android.accept_sdk_license = True

[android]
# ── WiFi / 位置权限（Android 10+ 扫描 WiFi 必须）──
android.permissions = ACCESS_FINE_LOCATION, ACCESS_COARSE_LOCATION, ACCESS_WIFI_STATE, CHANGE_WIFI_STATE, ACCESS_NETWORK_STATE, INTERNET

android.api = 33
android.minapi = 24
android.ndk = 25b
android.sdk = 33

# 只打 arm64（主流手机架构，减小包体积）
android.arch = arm64-v8a

android.allow_backup = True
android.stringversion = 1.0.0
android.version_code = 1

# 日志级别
android.logcat_filters = *:S python:D

[buildozer]
log_level = 2
warn_on_root = 1
