[app]
title = 采灵
package.name = cailing
package.domain = com.cailing

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,html,db,java

version = 1.0.0

# 依赖（pyjnius 已在 Kivy 中自带）
requirements = python3,kivy,pyjnius

orientation = portrait
fullscreen = 0
allow_screenshots = 1

# 自动接受 Android SDK 许可证（CI 自动化必需，必须在 [app] 节）
android.accept_sdk_license = True

# Java 源码（JsBridge 等 — 必须在 [app] 节下）
android.add_src = source/java

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

# 启用 AndroidX + Material Components
android.gradle_dependencies = androidx.appcompat:appcompat:1.6.1,com.google.android.material:material:1.9.0

# 日志级别
android.logcat_filters = *:S python:D

[buildozer]
log_level = 2
warn_on_root = 1
