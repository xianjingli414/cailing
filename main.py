# -*- coding: utf-8 -*-
"""
采灵 v12.0 — Flask + WebView Bootstrap 方案（WifiBridge原生扫描版）

核心改动：WiFi扫描通过Java原生WifiBridge（addJavascriptInterface）完成，
不再依赖Python/jnius调用Android API（v10-v11均因此失败）。

架构：
- Java层：WifiBridge.java 通过 addJavascriptInterface 注入WebView
- JS层：直接调用 window.WifiBridge.scanWifi() 获取WiFi列表
- Python层：接收JS传来的WiFi数据，负责同步到服务器
"""

import os
import sys
import json
import traceback
import threading
import time

from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder='.', static_url_path='')

# ── 判断平台 ──
IS_ANDROID = False
try:
    import jnius
    IS_ANDROID = True
except ImportError:
    pass

# ── 服务器配置 ──
SERVER_URL = os.environ.get("CAILING_SERVER", "http://121.4.28.216:8080")
PORT = int(os.environ.get("CAILING_PORT", "5000"))

# ── 权限状态缓存 ──
_last_permission_check = 0
_permission_granted = False

def _check_android_permission(perm_name):
    """检查 Android 权限是否已授予"""
    try:
        from jnius import autoclass
        Context = autoclass('android.content.Context')
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        activity = PythonActivity.mActivity
        if not activity:
            return False
        # 使用 ContextCompat.checkSelfPermission（兼容所有 Android 版本）
        try:
            ContextCompat = autoclass('androidx.core.content.ContextCompat')
            result = ContextCompat.checkSelfPermission(activity, perm_name)
            return result == 0  # PERMISSION_GRANTED = 0
        except Exception:
            # 降级：直接用 Context.checkSelfPermission（API 23+）
            try:
                result = activity.checkSelfPermission(perm_name)
                return result == 0
            except Exception:
                return False
    except Exception:
        return False


def _request_android_permissions():
    """请求 Android 运行时权限（通过 UI 线程）"""
    try:
        from jnius import autoclass
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        activity = PythonActivity.mActivity
        if not activity:
            print('[Cailing] mActivity is null, cannot request permissions')
            return

        def _do_request():
            try:
                activity.requestPermissions([
                    'android.permission.ACCESS_FINE_LOCATION',
                    'android.permission.ACCESS_COARSE_LOCATION',
                    'android.permission.ACCESS_WIFI_STATE',
                    'android.permission.CHANGE_WIFI_STATE',
                ])
                print('[Cailing] Permissions requested')
            except Exception as e:
                print('[Cailing] Permission request failed: ' + str(e))

        # 在 UI 线程上调用
        activity.runOnUiThread(_do_request)
    except Exception as e:
        print('[Cailing] Permission setup failed: ' + str(e))


def _ensure_permissions():
    """确保权限已授予，如果没有则请求"""
    global _last_permission_check, _permission_granted
    now = time.time()
    # 每 30 秒最多检查一次权限
    if now - _last_permission_check < 30 and _permission_granted:
        return True
    _last_permission_check = now

    fine_location = _check_android_permission('android.permission.ACCESS_FINE_LOCATION')
    wifi_state = _check_android_permission('android.permission.ACCESS_WIFI_STATE')

    if fine_location and wifi_state:
        _permission_granted = True
        return True

    # 权限不足，请求权限
    _permission_granted = False
    _request_android_permissions()
    return False


# ══════════════════════════════════════════════════════════
#  页面路由
# ══════════════════════════════════════════════════════════

@app.route('/')
def index():
    """提供 index.html 主页面"""
    return send_from_directory('.', 'index.html')


@app.route('/<path:path>')
def static_files(path):
    """提供静态文件"""
    return send_from_directory('.', path)


# ══════════════════════════════════════════════════════════
#  API 路由 — WiFi 扫描
# ══════════════════════════════════════════════════════════

@app.route('/api/wifi/scan', methods=['POST'])
def api_wifi_scan():
    """WiFi 扫描 — 读取系统缓存的扫描结果"""
    try:
        from wifi_scanner import _scan_android
        max_results = request.get_json(silent=True).get('max_results', 10) if request.is_json else 10
        r = _scan_android(max_results=max_results)
        if not r:
            return jsonify({"status": "empty", "message": "未扫描到WiFi，请确认已授予位置权限且WiFi已开启"})
        return jsonify(r)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/wifi/scan-and-sync', methods=['POST'])
def api_wifi_scan_and_sync():
    """WiFi 扫描 + 同步服务器"""
    try:
        from wifi_scanner import _scan_android
        import api_client
        max_results = request.get_json(silent=True).get('max_results', 10) if request.is_json else 10
        r = _scan_android(max_results=max_results)
        if not r:
            return jsonify({"status": "empty", "message": "未扫描到WiFi，请确认已授予位置权限且WiFi已开启"})
        sync_ok = sync_skip = 0
        synced = False
        if api_client.is_logged_in():
            try:
                sync_ok, sync_skip = api_client.upload_wifi_records(r)
                synced = True
            except Exception:
                pass
        return jsonify({
            "data": r, "synced": synced,
            "sync_ok": sync_ok, "sync_skip": sync_skip
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})


# ══════════════════════════════════════════════════════════
#  API 路由 — 认证（代理到远程服务器）
#  关键：登录 API 的返回格式必须与 index.html 的 doLogin() 兼容
# ══════════════════════════════════════════════════════════

@app.route('/api/login', methods=['POST'])
def api_login():
    """
    登录 — 代理到远程服务器
    服务器返回 {"success":True,"user":{"username":"...","displayName":"...","role":"..."}}
    需要转换为 index.html 期望的 {"success":True,"result":{...}} 格式
    """
    try:
        import api_client
        data = request.get_json(force=True)
        username = data.get('username', '')
        password = data.get('password', '')
        ok, result = api_client.login(username, password)
        if ok:
            # result 是 api_client.get_current_user() 返回的 dict
            # 确保包含 displayName 字段（index.html 依赖此字段）
            return jsonify({"success": True, "result": result})
        else:
            return jsonify({"success": False, "error": str(result)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/logout', methods=['POST'])
def api_logout():
    """登出"""
    try:
        import api_client
        api_client.logout()
    except Exception:
        pass
    return jsonify({"success": True})


@app.route('/api/is-logged-in', methods=['GET'])
def api_is_logged_in():
    """检查登录状态"""
    try:
        import api_client
        logged = api_client.is_logged_in()
        user = api_client.get_current_user()
        return jsonify({"logged_in": logged, "user": user})
    except Exception:
        return jsonify({"logged_in": False, "user": None})


# ══════════════════════════════════════════════════════════
#  API 路由 — 配置
# ══════════════════════════════════════════════════════════

@app.route('/api/server-url', methods=['GET'])
def api_get_server_url():
    """获取服务器地址"""
    try:
        import api_client
        return jsonify({"url": api_client.get_server_url()})
    except Exception:
        return jsonify({"url": SERVER_URL})


@app.route('/api/server-url', methods=['PUT'])
def api_set_server_url():
    """设置服务器地址"""
    try:
        import api_client
        data = request.get_json(force=True)
        url = data.get('url', '')
        api_client.set_server_url(url)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ══════════════════════════════════════════════════════════
#  API 路由 — WiFi 数据管理（代理到远程服务器）
# ══════════════════════════════════════════════════════════

@app.route('/api/wifi', methods=['GET'])
def api_wifi_query():
    """查询 WiFi 记录"""
    try:
        import api_client
        if not api_client.is_logged_in():
            return jsonify({"records": [], "total": 0, "page": 1, "page_size": 50})
        ssid = request.args.get('ssid', '')
        bssid = request.args.get('bssid', '')
        band = request.args.get('band', '')
        unit_id = request.args.get('unit_id', type=int)
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 50, type=int)
        records, total = api_client.query_wifi_server(
            ssid=ssid, bssid=bssid, band=band,
            unit_id=unit_id, page=page, page_size=page_size
        )
        return jsonify({"records": records, "total": total, "page": page, "page_size": page_size})
    except Exception as e:
        return jsonify({"records": [], "total": 0, "page": 1, "page_size": 50, "error": str(e)})


@app.route('/api/wifi/<int:wifi_id>/bind', methods=['PUT'])
def api_wifi_bind(wifi_id):
    """绑定 WiFi 到单位"""
    try:
        import api_client
        data = request.get_json(force=True)
        unit_id = data.get('unit_id')
        ok = api_client.bind_wifi_server(wifi_id, unit_id)
        return jsonify({"success": ok})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/wifi/bulk-bind', methods=['POST'])
def api_wifi_bulk_bind():
    """批量绑定 WiFi"""
    try:
        import api_client
        data = request.get_json(force=True)
        bssids = data.get('bssids', [])
        unit_id = data.get('unit_id')
        count = api_client.bulk_bind_wifi_server(bssids, unit_id)
        return jsonify({"count": count})
    except Exception as e:
        return jsonify({"count": 0, "error": str(e)})


@app.route('/api/wifi/<int:wifi_id>', methods=['DELETE'])
def api_wifi_delete(wifi_id):
    """删除 WiFi 记录"""
    try:
        import api_client
        ok = api_client.delete_wifi_server(wifi_id)
        return jsonify({"success": ok})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/wifi/bssid/<bssid>', methods=['DELETE'])
def api_wifi_delete_by_bssid(bssid):
    """按BSSID删除WiFi记录"""
    try:
        import api_client
        ok = api_client.delete_wifi_by_bssid_server(bssid)
        return jsonify({"success": ok})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/wifi/bulk-delete', methods=['POST'])
def api_wifi_bulk_delete():
    """批量删除 WiFi 记录"""
    try:
        import api_client
        data = request.get_json(force=True)
        ids = data.get('ids', [])
        ok = api_client.bulk_delete_wifi_server(ids)
        return jsonify({"success": ok})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ══════════════════════════════════════════════════════════
#  API 路由 — 单位管理（代理到远程服务器）
# ══════════════════════════════════════════════════════════

@app.route('/api/units', methods=['GET'])
def api_units_query():
    """查询单位列表"""
    try:
        import api_client
        if not api_client.is_logged_in():
            return jsonify({"records": [], "total": 0})
        keyword = request.args.get('keyword', '')
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 50, type=int)
        records, total = api_client.query_units_server(
            keyword=keyword, page=page, page_size=page_size
        )
        return jsonify({"records": records, "total": total})
    except Exception as e:
        return jsonify({"records": [], "total": 0, "error": str(e)})


@app.route('/api/units/all', methods=['GET'])
def api_units_all():
    """获取全部单位"""
    try:
        import api_client
        units = api_client.get_all_units_server()
        return jsonify({"units": units})
    except Exception:
        return jsonify({"units": []})


@app.route('/api/units', methods=['POST'])
def api_units_add():
    """新增单位"""
    try:
        import api_client
        data = request.get_json(force=True)
        ok = api_client.add_unit_server(
            data.get('unit_name', ''),
            data.get('credit_code', ''),
            data.get('address', '')
        )
        return jsonify({"success": ok})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/units/<int:uid>', methods=['PUT'])
def api_units_update(uid):
    """修改单位"""
    try:
        import api_client
        data = request.get_json(force=True)
        ok = api_client.update_unit_server(
            uid,
            data.get('unit_name', ''),
            data.get('credit_code', ''),
            data.get('address', '')
        )
        return jsonify({"success": ok})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/units/<int:uid>', methods=['DELETE'])
def api_units_delete(uid):
    """删除单位"""
    try:
        import api_client
        ok = api_client.delete_unit_server(uid)
        return jsonify({"success": ok})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/units/name/<unit_name>', methods=['DELETE'])
def api_units_delete_by_name(unit_name):
    """按单位名称删除"""
    try:
        import api_client
        ok = api_client.delete_unit_by_name_server(unit_name)
        return jsonify({"success": ok})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ══════════════════════════════════════════════════════════
#  API 路由 — 用户管理（代理到远程服务器）
#  【新增】v10.1 — 修复密码修改和用户删除
# ══════════════════════════════════════════════════════════

@app.route('/api/users', methods=['GET'])
def api_users_list():
    """获取用户列表"""
    try:
        import api_client
        users = api_client.get_users_server()
        return jsonify(users)
    except Exception as e:
        return jsonify([])


@app.route('/api/users', methods=['POST'])
def api_users_add():
    """新增用户（管理员）"""
    try:
        import api_client
        data = request.get_json(force=True)
        ok, msg = api_client.add_user_server(
            data.get('display_name', ''),
            data.get('username', ''),
            data.get('password', '')
        )
        return jsonify({"success": ok, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/users/<username>', methods=['DELETE'])
def api_users_delete(username):
    """删除用户（管理员）"""
    try:
        import api_client
        ok = api_client.delete_user_server(username)
        return jsonify({"success": ok})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/users/<username>/password', methods=['PUT'])
def api_users_change_password(username):
    """修改密码"""
    try:
        import api_client
        data = request.get_json(force=True)
        ok, msg = api_client.change_password_server(
            username,
            data.get('old_password', ''),
            data.get('new_password', '')
        )
        return jsonify({"success": ok, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/users/<username>/perms', methods=['PUT'])
def api_users_set_perms(username):
    """设置用户权限（管理员）"""
    try:
        import api_client
        data = request.get_json(force=True)
        ok = api_client.set_user_perms_server(username, data)
        return jsonify({"success": ok})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/wifi/sync', methods=['POST'])
def api_wifi_sync():
    """接收JS从WifiBridge获取的WiFi数据，同步到服务器"""
    try:
        import api_client
        data = request.get_json(force=True)
        records = data.get('records', [])
        if not records:
            return jsonify({"ok": 0, "skip": 0, "update": 0})
        if api_client.is_logged_in():
            sync_ok, sync_skip = api_client.upload_wifi_records(records)
            return jsonify({"ok": sync_ok, "skip": sync_skip, "update": 0})
        return jsonify({"ok": 0, "skip": len(records), "update": 0})
    except Exception as e:
        return jsonify({"ok": 0, "skip": 0, "update": 0, "error": str(e)})


# ══════════════════════════════════════════════════════════
#  API 路由 — 统计 & 导出
# ══════════════════════════════════════════════════════════

@app.route('/api/stats', methods=['GET'])
def api_stats():
    """获取统计数据"""
    try:
        import api_client
        stats = api_client.get_stats_server()
        return jsonify(stats)
    except Exception:
        return jsonify({})


@app.route('/api/export', methods=['GET'])
def api_export():
    """导出 WiFi 数据 CSV"""
    try:
        import tempfile
        import api_client
        fmt = request.args.get('format', 'csv')
        filepath = os.path.join(tempfile.gettempdir(), 'wifi_export.csv')
        ok, result = api_client.export_wifi_server(filepath)
        if ok:
            return send_from_directory(tempfile.gettempdir(), 'wifi_export.csv', as_attachment=True)
        else:
            return jsonify({"success": False, "error": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ══════════════════════════════════════════════════════════
#  API 路由 — 权限检查（前端可调用确认权限已授予）
# ══════════════════════════════════════════════════════════

@app.route('/api/request-permissions', methods=['POST'])
def api_request_permissions():
    """前端触发权限请求"""
    try:
        _request_android_permissions()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/check-permissions', methods=['GET'])
def api_check_permissions():
    """检查位置权限是否已授予"""
    try:
        fine_location = _check_android_permission('android.permission.ACCESS_FINE_LOCATION')
        wifi_state = _check_android_permission('android.permission.ACCESS_WIFI_STATE')
        coarse_location = _check_android_permission('android.permission.ACCESS_COARSE_LOCATION')

        missing = []
        if not fine_location:
            missing.append('android.permission.ACCESS_FINE_LOCATION')
        if not coarse_location:
            missing.append('android.permission.ACCESS_COARSE_LOCATION')
        if not wifi_state:
            missing.append('android.permission.ACCESS_WIFI_STATE')

        granted = len(missing) == 0
        return jsonify({"granted": granted, "missing": missing})
    except Exception as e:
        return jsonify({"granted": False, "error": str(e)})


@app.route('/api/current-user', methods=['GET'])
def api_current_user():
    """获取当前登录用户信息"""
    try:
        import api_client
        user = api_client.get_current_user()
        if user:
            return jsonify(user)
        return jsonify({"error": "未登录"}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 401


@app.route('/api/config', methods=['GET'])
def api_config_get():
    """获取配置"""
    try:
        import api_client
        config = api_client.get_config_server()
        return jsonify(config)
    except Exception:
        return jsonify({})


@app.route('/api/config', methods=['PUT'])
def api_config_set():
    """设置配置"""
    try:
        import api_client
        data = request.get_json(force=True)
        ok = api_client.set_config_server(data)
        return jsonify({"success": ok})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ══════════════════════════════════════════════════════════
#  启动
# ══════════════════════════════════════════════════════════

def main():
    print('[Cailing] === v15.0 Flask Server ===')
    print(f'[Cailing] IS_ANDROID={IS_ANDROID}')
    print('[Cailing] WiFi扫描已改用Java原生WifiBridge，JS直接调用window.WifiBridge.scanWifi()')

    # Android 权限请求（延迟3秒，等 Activity 完全初始化）
    if IS_ANDROID:
        def _delayed_request():
            time.sleep(3.0)
            _request_android_permissions()
        t = threading.Thread(target=_delayed_request, daemon=True)
        t.start()

    # 启动 Flask（阻塞）
    print(f'[Cailing] Starting Flask on http://127.0.0.1:{PORT}')
    app.run(host='127.0.0.1', port=PORT, debug=False, threaded=True)


if __name__ == '__main__':
    main()
