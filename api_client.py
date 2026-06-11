# -*- coding: utf-8 -*-
"""
api_client.py - 采灵服务端API客户端
APP端数据通过此模块同步到服务器数据库
"""

import json
import urllib.request
import urllib.error
import os

# ── 服务器配置 ─────────────────────────────────────────────
# 生产环境地址（根据实际部署修改）
SERVER_URL = os.environ.get("CAILING_SERVER", "http://121.4.28.216:8080")

# 全局登录状态
_auth_token = None
_current_user = None


def get_server_url():
    return SERVER_URL


def set_server_url(url):
    global SERVER_URL
    SERVER_URL = url.rstrip("/")


def get_token():
    return _auth_token


def get_current_user():
    return _current_user


def is_logged_in():
    return _auth_token is not None


# ── HTTP 请求封装 ──────────────────────────────────────────

def _request(method, path, data=None, files=None):
    """
    统一HTTP请求
    method: GET/POST/PUT/DELETE
    path: API路径，如 /api/wifi
    data: dict，JSON请求体
    files: dict，文件上传 {field_name: (filename, bytes_data)}
    返回: (status_code, response_dict_or_None)
    """
    url = SERVER_URL + path
    headers = {}

    if _auth_token:
        # Cookie方式
        headers["Cookie"] = f"cailing_token={_auth_token}"

    if files:
        # multipart/form-data
        import uuid
        boundary = uuid.uuid4().hex
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        body = b""
        for key, (fname, fdata) in files.items():
            body += f"--{boundary}\r\n".encode()
            body += f'Content-Disposition: form-data; name="{key}"; filename="{fname}"\r\n'.encode()
            body += b"Content-Type: application/octet-stream\r\n\r\n"
            body += fdata
            body += b"\r\n"
        if data:
            body += f"--{boundary}\r\n".encode()
            body += f'Content-Disposition: form-data; name="data"\r\n\r\n'.encode()
            body += json.dumps(data).encode()
            body += b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
    elif data is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
    else:
        req = urllib.request.Request(url, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            # 提取Set-Cookie中的token
            set_cookie = resp.headers.get("Set-Cookie", "")
            global _auth_token
            if "cailing_token=" in set_cookie:
                import re
                m = re.search(r"cailing_token=([^;]+)", set_cookie)
                if m:
                    _auth_token = m.group(1)
            try:
                result = json.loads(resp.read().decode("utf-8"))
            except Exception:
                result = None
            return status, result
    except urllib.error.HTTPError as e:
        try:
            error_body = json.loads(e.read().decode("utf-8"))
        except Exception:
            error_body = {"error": str(e)}
        return e.code, error_body
    except urllib.error.URLError as e:
        return 0, {"error": f"网络错误: {e.reason}"}
    except Exception as e:
        return 0, {"error": str(e)}


# ══════════════════════════════════════════════════════════
#  认证 API
# ══════════════════════════════════════════════════════════

def login(username, password):
    """
    登录服务器
    返回: (成功?, 用户信息或错误消息)
    """
    global _auth_token, _current_user
    status, data = _request("POST", "/api/login", {"username": username, "password": password})
    if status == 200 and data and data.get("success"):
        _current_user = data.get("user", {})
        # token 可能从response header或body获取
        return True, _current_user
    else:
        _auth_token = None
        _current_user = None
        error = data.get("error", "登录失败") if data else "网络错误"
        return False, error


def logout():
    """登出服务器"""
    global _auth_token, _current_user
    if _auth_token:
        _request("POST", "/api/logout")
    _auth_token = None
    _current_user = None


def get_current_user_info():
    """获取当前登录用户信息"""
    status, data = _request("GET", "/api/current-user")
    if status == 200:
        return data
    return None


# ══════════════════════════════════════════════════════════
#  WiFi 数据 API
# ══════════════════════════════════════════════════════════

def upload_wifi_records(records):
    """
    上传WiFi采集记录到服务器
    records: list of dict [{ssid, bssid, signal, channel, encrypt_type, band, vendor}]
    返回: (成功数, 跳过数)
    """
    if not _auth_token:
        return 0, len(records)
    status, data = _request("POST", "/api/wifi", {"records": records})
    if status == 200 and data:
        return data.get("ok", 0), data.get("skip", 0)
    return 0, len(records)


def query_wifi_server(ssid="", bssid="", band="", unit_id=None, bound="", page=1, page_size=50):
    """
    从服务器查询WiFi记录
    返回: (records_list, total)
    """
    if not _auth_token:
        return [], 0
    params = []
    if ssid: params.append(f"ssid={urllib.parse.quote(ssid)}")
    if bssid: params.append(f"bssid={urllib.parse.quote(bssid)}")
    if band: params.append(f"band={band}")
    if unit_id is not None: params.append(f"unit_id={unit_id}")
    if bound: params.append(f"bound={bound}")
    params.append(f"page={page}")
    params.append(f"page_size={page_size}")
    qs = "&".join(params)
    path = f"/api/wifi?{qs}" if qs else "/api/wifi"
    status, data = _request("GET", path)
    if status == 200 and data:
        return data.get("records", []), data.get("total", 0)
    return [], 0


def delete_wifi_server(wifi_id):
    """从服务器删除WiFi记录"""
    if not _auth_token:
        return False
    status, _ = _request("DELETE", f"/api/wifi/{wifi_id}")
    return status == 200


def bulk_delete_wifi_server(ids):
    """从服务器批量删除WiFi记录"""
    if not _auth_token:
        return False
    status, _ = _request("POST", "/api/wifi/bulk-delete", {"ids": ids})
    return status == 200


def bind_wifi_server(wifi_id, unit_id):
    """在服务器上绑定WiFi到单位"""
    if not _auth_token:
        return False
    status, _ = _request("PUT", f"/api/wifi/{wifi_id}/bind", {"unit_id": unit_id})
    return status == 200


def bulk_bind_wifi_server(bssids, unit_id):
    """在服务器上批量绑定WiFi到单位"""
    if not _auth_token:
        return 0
    status, data = _request("POST", "/api/wifi/bulk-bind", {"bssids": bssids, "unit_id": unit_id})
    if status == 200 and data:
        return data.get("count", 0)
    return 0


# ══════════════════════════════════════════════════════════
#  单位管理 API
# ══════════════════════════════════════════════════════════

def query_units_server(keyword="", page=1, page_size=50):
    """从服务器查询单位列表"""
    if not _auth_token:
        return [], 0
    params = []
    if keyword: params.append(f"keyword={urllib.parse.quote(keyword)}")
    params.append(f"page={page}")
    params.append(f"page_size={page_size}")
    qs = "&".join(params)
    path = f"/api/units?{qs}" if qs else "/api/units"
    status, data = _request("GET", path)
    if status == 200 and data:
        return data.get("records", []), data.get("total", 0)
    return [], 0


def get_all_units_server():
    """从服务器获取全部单位（下拉选择用）"""
    if not _auth_token:
        return []
    status, data = _request("GET", "/api/units/all")
    if status == 200 and isinstance(data, list):
        return data
    return []


def add_unit_server(name, code, addr):
    """在服务器上新增单位"""
    if not _auth_token:
        return False
    status, _ = _request("POST", "/api/units", {"unit_name": name, "credit_code": code, "address": addr})
    return status == 200


def update_unit_server(uid, name, code, addr):
    """在服务器上修改单位"""
    if not _auth_token:
        return False
    status, _ = _request("PUT", f"/api/units/{uid}", {"unit_name": name, "credit_code": code, "address": addr})
    return status == 200


def delete_unit_server(uid):
    """在服务器上删除单位"""
    if not _auth_token:
        return False
    status, _ = _request("DELETE", f"/api/units/{uid}")
    return status == 200


def import_units_server(filepath):
    """上传文件到服务器导入单位"""
    if not _auth_token:
        return 0, 1, ["未登录"]
    with open(filepath, "rb") as f:
        file_data = f.read()
    filename = os.path.basename(filepath)
    status, data = _request("POST", "/api/units/import", files={"file": (filename, file_data)})
    if status == 200 and data:
        return data.get("ok", 0), data.get("fail", 0), data.get("reasons", [])
    return 0, 1, ["上传失败"]


# ══════════════════════════════════════════════════════════
#  导出 API
# ══════════════════════════════════════════════════════════

def export_wifi_server(filepath):
    """从服务器导出WiFi数据CSV"""
    if not _auth_token:
        return False, "未登录"
    url = SERVER_URL + "/api/export?format=csv"
    headers = {}
    if _auth_token:
        headers["Cookie"] = f"cailing_token={_auth_token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            with open(filepath, "wb") as f:
                f.write(data)
            return True, filepath
    except Exception as e:
        return False, str(e)


# ══════════════════════════════════════════════════════════
#  用户管理 API
# ══════════════════════════════════════════════════════════

def get_users_server():
    """获取服务器上用户列表（管理员）"""
    if not _auth_token:
        return []
    status, data = _request("GET", "/api/users")
    if status == 200 and isinstance(data, list):
        return data
    return []


def add_user_server(display_name, username, password):
    """在服务器上新增用户（管理员）"""
    if not _auth_token:
        return False, "未登录"
    status, data = _request("POST", "/api/users", {"display_name": display_name, "username": username, "password": password})
    if status == 200:
        return True, "创建成功"
    return False, data.get("error", "创建失败") if data else "网络错误"


def delete_user_server(username):
    """在服务器上删除用户（管理员）"""
    if not _auth_token:
        return False
    status, _ = _request("DELETE", f"/api/users/{username}")
    return status == 200


def change_password_server(username, old_password, new_password):
    """修改密码"""
    if not _auth_token:
        return False, "未登录"
    status, data = _request("PUT", f"/api/users/{username}/password", {"old_password": old_password, "new_password": new_password})
    if status == 200:
        return True, "修改成功"
    return False, data.get("error", "修改失败") if data else "网络错误"


def set_user_perms_server(username, perms):
    """设置用户权限（管理员）"""
    if not _auth_token:
        return False
    status, _ = _request("PUT", f"/api/users/{username}/perms", perms)
    return status == 200


# ══════════════════════════════════════════════════════════
#  统计 & 配置 API
# ══════════════════════════════════════════════════════════

def get_stats_server():
    """获取服务器端统计数据"""
    if not _auth_token:
        return {}
    status, data = _request("GET", "/api/stats")
    if status == 200:
        return data
    return {}


def get_config_server():
    """获取服务器端配置"""
    if not _auth_token:
        return {}
    status, data = _request("GET", "/api/config")
    if status == 200:
        return data
    return {}


def set_config_server(config):
    """更新服务器端配置"""
    if not _auth_token:
        return False
    status, _ = _request("PUT", "/api/config", config)
    return status == 200


# ══════════════════════════════════════════════════════════
#  便捷函数：同步本地操作到服务器
# ══════════════════════════════════════════════════════════

def sync_save_wifi_records(records):
    """
    保存WiFi记录：本地数据库 + 服务器同步
    records: list of dict
    返回: (本地成功数, 本地跳过数, 服务器成功数, 服务器跳过数)
    """
    import database
    # 1. 写入本地数据库
    ok_local, skip_local = database.save_wifi_records(records)
    # 2. 同步到服务器
    ok_server, skip_server = upload_wifi_records(records)
    return ok_local, skip_local, ok_server, skip_server


def sync_bind_wifi(wifi_id, unit_id):
    """
    绑定WiFi到单位：本地 + 服务器
    """
    import database
    database.bind_wifi_unit(wifi_id, unit_id)
    bind_wifi_server(wifi_id, unit_id)


def sync_add_unit(name, code, addr):
    """
    新增单位：本地 + 服务器
    """
    import database
    ok = database.add_unit(name, code, addr)
    if ok:
        add_unit_server(name, code, addr)
    return ok


def sync_update_unit(uid, name, code, addr):
    """
    修改单位：本地 + 服务器
    """
    import database
    ok = database.update_unit(uid, name, code, addr)
    if ok:
        update_unit_server(uid, name, code, addr)
    return ok


def sync_delete_unit(uid):
    """
    删除单位：本地 + 服务器
    """
    import database
    database.delete_unit(uid)
    delete_unit_server(uid)


def sync_delete_wifi(wifi_id):
    """
    删除WiFi记录：本地 + 服务器
    """
    import database
    database.delete_wifi(wifi_id)
    delete_wifi_server(wifi_id)


def sync_delete_units_by_ids(ids):
    """
    批量删除单位：本地 + 服务器
    """
    import database
    database.delete_units_by_ids(ids)
    for uid in ids:
        delete_unit_server(uid)
