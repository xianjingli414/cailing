# -*- coding: utf-8 -*-
"""
采灵 v10.1 — Flask + WebView Bootstrap 方案（功能修复版）

修复 v10.0 的问题：
1. 权限请求未弹出 → 适配 webview bootstrap 的 Activity 类名
2. test 用户无法登录 → 修复登录 API 返回格式匹配
3. 密码修改和用户删除 → 新增用户管理 API 路由
"""

import os
import sys
import json
import traceback
import threading

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

# ── 权限请求（Android）──
_perms_requested = False

def _request_android_permissions():
    """请求 Android 运行时权限（通过 UI 线程）"""
    global _perms_requested
    if _perms_requested or not IS_ANDROID:
        return
    _perms_requested = True

    def _do_request():
        try:
            from jnius import autoclass
            activity = autoclass('org.kivy.android.PythonActivity').mActivity
            if activity:
                # 在 UI 线程上调用 requestPermissions
                activity.runOnUiThread(lambda: activity.requestPermissions([
                    'android.permission.ACCESS_FINE_LOCATION',
                    'android.permission.ACCESS_COARSE_LOCATION',
                    'android.permission.ACCESS_WIFI_STATE',
                    'android.permission.CHANGE_WIFI_STATE',
                ]))
                print('[Cailing] Permissions requested via UI thread')
            else:
                print('[Cailing] mActivity is null, cannot request permissions')
        except Exception as e:
            print('[Cailing] Permission request failed: ' + str(e))

    # 在子线程中延迟请求（等 Activity 完全初始化）
    t = threading.Timer(2.0, _do_request)
    t.daemon = True
    t.start()


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
    """WiFi 扫描"""
    try:
        from wifi_scanner import _scan_android
        r = _scan_android()
        if not r:
            return jsonify({"status": "empty", "message": "未扫描到WiFi"})
        return jsonify(r)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/wifi/scan-and-sync', methods=['POST'])
def api_wifi_scan_and_sync():
    """WiFi 扫描 + 同步服务器"""
    try:
        from wifi_scanner import _scan_android
        import api_client
        r = _scan_android()
        if not r:
            return jsonify({"status": "empty", "message": "未扫描到WiFi"})
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
            return jsonify({"records": [], "total": 0})
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
        return jsonify({"records": records, "total": total})
    except Exception as e:
        return jsonify({"records": [], "total": 0, "error": str(e)})


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
        return jsonify(units)
    except Exception:
        return jsonify([])


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
        _perms_requested = False  # 重置标记允许重新请求
        _request_android_permissions()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/check-permissions', methods=['GET'])
def api_check_permissions():
    """检查位置权限是否已授予"""
    try:
        from jnius import autoclass
        activity = autoclass('org.kivy.android.PythonActivity').mActivity
        if not activity:
            return jsonify({"granted": False, "error": "Activity not found"})

        granted = True
        missing = []
        for perm in ['android.permission.ACCESS_FINE_LOCATION', 'android.permission.ACCESS_COARSE_LOCATION']:
            result = activity.checkCurrentPermission(perm)
            if not result:
                granted = False
                missing.append(perm)

        return jsonify({"granted": granted, "missing": missing})
    except Exception as e:
        return jsonify({"granted": False, "error": str(e)})


# ══════════════════════════════════════════════════════════
#  启动
# ══════════════════════════════════════════════════════════

def main():
    print('[Cailing] === v10.1 Flask Server ===')
    print(f'[Cailing] IS_ANDROID={IS_ANDROID}')

    # Android 权限请求
    if IS_ANDROID:
        _request_android_permissions()

    # 启动 Flask（阻塞）
    print(f'[Cailing] Starting Flask on http://127.0.0.1:{PORT}')
    app.run(host='127.0.0.1', port=PORT, debug=False, threaded=True)


if __name__ == '__main__':
    main()
