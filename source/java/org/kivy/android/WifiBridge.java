package org.kivy.android;

import android.app.Activity;
import android.content.Context;
import android.net.wifi.ScanResult;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.Manifest;
import android.content.pm.PackageManager;
import android.os.Build;
import android.util.Log;
import android.webkit.JavascriptInterface;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.List;

/**
 * WifiBridge - JavaScript接口，让WebView中的JS直接调用Android原生WiFi API
 *
 * 在PythonActivity中通过 addJavascriptInterface 注入到WebView
 * JS调用方式: window.WifiBridge.scanWifi()
 *             window.WifiBridge.getConnectionInfo()
 *             window.WifiBridge.hasLocationPermission()
 *             window.WifiBridge.requestLocationPermission()
 */
public class WifiBridge {
    private static final String TAG = "WifiBridge";
    private Activity mActivity;

    public WifiBridge(Activity activity) {
        mActivity = activity;
    }

    /**
     * 扫描WiFi - 读取系统缓存的扫描结果
     * 返回JSON字符串，包含WiFi列表
     */
    @JavascriptInterface
    public String scanWifi() {
        JSONArray results = new JSONArray();
        try {
            WifiManager wifiMgr = (WifiManager) mActivity.getApplicationContext()
                    .getSystemService(Context.WIFI_SERVICE);

            if (wifiMgr == null) {
                Log.e(TAG, "WifiManager is null");
                return results.toString();
            }

            // 尝试触发扫描（静默失败）
            try {
                wifiMgr.startScan();
            } catch (Exception e) {
                Log.w(TAG, "startScan failed (expected on some ROMs): " + e.getMessage());
            }

            // 读取系统缓存的扫描结果
            List<ScanResult> scanResults = wifiMgr.getScanResults();
            Log.i(TAG, "getScanResults returned " + (scanResults != null ? scanResults.size() : 0) + " results");

            if (scanResults != null) {
                for (ScanResult r : scanResults) {
                    try {
                        JSONObject item = new JSONObject();
                        String ssid = r.SSID != null ? r.SSID : "<Hidden>";
                        String bssid = r.BSSID != null ? r.BSSID.toUpperCase() : "";

                        if (bssid.isEmpty()) continue;

                        // 频率转信道
                        int freq = r.frequency;
                        int ch = channelFromFreq(freq);
                        String band = ch > 14 ? "5G" : "2.4G";

                        // 加密类型
                        String caps = r.capabilities != null ? r.capabilities : "";
                        String enc;
                        if (caps.contains("WPA3")) enc = "WPA3";
                        else if (caps.contains("WPA2")) enc = "WPA2";
                        else if (caps.contains("WPA")) enc = "WPA/WPA2";
                        else if (caps.contains("WEP")) enc = "WEP";
                        else enc = "开放";

                        item.put("ssid", ssid);
                        item.put("bssid", bssid);
                        item.put("signal", r.level);
                        item.put("channel", ch);
                        item.put("encrypt_type", enc);
                        item.put("band", band);
                        item.put("vendor", getVendor(bssid));
                        item.put("frequency", freq);
                        results.put(item);
                    } catch (Exception e) {
                        Log.w(TAG, "Error parsing scan result: " + e.getMessage());
                    }
                }
            }

            // 补充当前连接的WiFi
            try {
                WifiInfo wifiInfo = wifiMgr.getConnectionInfo();
                if (wifiInfo != null) {
                    String connBssid = wifiInfo.getBSSID() != null ? wifiInfo.getBSSID().toUpperCase() : "";
                    String connSsid = wifiInfo.getSSID() != null ? wifiInfo.getSSID() : "";
                    // Android 8.0+ SSID带引号
                    if (connSsid.startsWith("\"") && connSsid.endsWith("\"")) {
                        connSsid = connSsid.substring(1, connSsid.length() - 1);
                    }

                    if (!connBssid.isEmpty() && !connBssid.equals("00:00:00:00:00:00")) {
                        // 检查是否已在扫描结果中
                        boolean found = false;
                        for (int i = 0; i < results.length(); i++) {
                            JSONObject existing = results.getJSONObject(i);
                            if (connBssid.equals(existing.optString("bssid"))) {
                                found = true;
                                break;
                            }
                        }
                        if (!found) {
                            JSONObject connItem = new JSONObject();
                            connItem.put("ssid", connSsid.isEmpty() ? "<Connected>" : connSsid);
                            connItem.put("bssid", connBssid);
                            connItem.put("signal", wifiInfo.getRssi());
                            connItem.put("channel", 0);
                            connItem.put("encrypt_type", "WPA2");
                            connItem.put("band", "2.4G");
                            connItem.put("vendor", getVendor(connBssid));
                            connItem.put("frequency", 0);
                            results.put(connItem);
                            Log.i(TAG, "Added connected WiFi: " + connSsid + " (" + connBssid + ")");
                        }
                    }
                }
            } catch (Exception e) {
                Log.w(TAG, "getConnectionInfo error: " + e.getMessage());
            }

            Log.i(TAG, "scanWifi returning " + results.length() + " results");

        } catch (Exception e) {
            Log.e(TAG, "scanWifi error: " + e.getMessage());
        }
        return results.toString();
    }

    /**
     * 获取当前连接的WiFi信息
     */
    @JavascriptInterface
    public String getConnectionInfo() {
        JSONObject result = new JSONObject();
        try {
            WifiManager wifiMgr = (WifiManager) mActivity.getApplicationContext()
                    .getSystemService(Context.WIFI_SERVICE);
            if (wifiMgr == null) {
                result.put("error", "WifiManager is null");
                return result.toString();
            }
            WifiInfo wifiInfo = wifiMgr.getConnectionInfo();
            if (wifiInfo != null) {
                String ssid = wifiInfo.getSSID() != null ? wifiInfo.getSSID() : "";
                if (ssid.startsWith("\"") && ssid.endsWith("\"")) {
                    ssid = ssid.substring(1, ssid.length() - 1);
                }
                String bssid = wifiInfo.getBSSID() != null ? wifiInfo.getBSSID().toUpperCase() : "";
                result.put("ssid", ssid);
                result.put("bssid", bssid);
                result.put("rssi", wifiInfo.getRssi());
                result.put("connected", !bssid.isEmpty() && !bssid.equals("00:00:00:00:00:00"));
            }
        } catch (Exception e) {
            result.put("error", e.getMessage());
        }
        return result.toString();
    }

    /**
     * 检查是否有位置权限
     */
    @JavascriptInterface
    public boolean hasLocationPermission() {
        if (Build.VERSION.SDK_INT < 23) return true;
        return mActivity.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                == PackageManager.PERMISSION_GRANTED;
    }

    /**
     * 请求位置权限（在UI线程上）
     */
    @JavascriptInterface
    public void requestLocationPermission() {
        if (Build.VERSION.SDK_INT < 23) return;
        try {
            mActivity.runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    try {
                        mActivity.requestPermissions(
                                new String[]{
                                        Manifest.permission.ACCESS_FINE_LOCATION,
                                        Manifest.permission.ACCESS_COARSE_LOCATION
                                }, 1);
                        Log.i(TAG, "Location permission requested");
                    } catch (Exception e) {
                        Log.e(TAG, "requestLocationPermission error: " + e.getMessage());
                    }
                }
            });
        } catch (Exception e) {
            Log.e(TAG, "requestLocationPermission error: " + e.getMessage());
        }
    }

    /**
     * 检查WiFi是否开启
     */
    @JavascriptInterface
    public boolean isWifiEnabled() {
        try {
            WifiManager wifiMgr = (WifiManager) mActivity.getApplicationContext()
                    .getSystemService(Context.WIFI_SERVICE);
            return wifiMgr != null && wifiMgr.isWifiEnabled();
        } catch (Exception e) {
            return false;
        }
    }

    // ── 辅助方法 ──

    private static int channelFromFreq(int freq) {
        if (2412 <= freq && freq <= 2484) return (freq - 2412) / 5 + 1;
        if (freq == 2484) return 14;
        if (4915 <= freq && freq <= 4980) return (freq - 4915) / 5 + 183;
        if (5035 <= freq && freq <= 5980) return (freq - 5035) / 5 + 7;
        return 0;
    }

    private static String getVendor(String bssid) {
        if (bssid == null || bssid.isEmpty()) return "未知";
        String prefix = bssid.toUpperCase().replace(":", "").substring(0, 6);
        // 常见OUI厂商
        if (prefix.startsWith("005056")) return "VMware";
        if (prefix.startsWith("000C29")) return "VMware";
        if (prefix.startsWith("001A2B")) return "Cisco";
        if (prefix.startsWith("001B63")) return "Apple";
        if (prefix.startsWith("186590")) return "Apple";
        if (prefix.startsWith("F81EDF")) return "Apple";
        if (prefix.startsWith("2CAB00")) return "TP-Link";
        if (prefix.startsWith("54A703")) return "TP-Link";
        if (prefix.startsWith("001D0F")) return "D-Link";
        if (prefix.startsWith("20E52A")) return "Netgear";
        if (prefix.startsWith("001882")) return "Huawei";
        if (prefix.startsWith("EC8CA2")) return "ASUS";
        if (prefix.startsWith("286C07")) return "Xiaomi";
        if (prefix.startsWith("F8A45F")) return "Xiaomi";
        if (prefix.startsWith("CC81")) return "Huawei";
        if (prefix.startsWith("70A8")) return "Huawei";
        if (prefix.startsWith("48A1")) return "Huawei";
        if (prefix.startsWith("3468")) return "Huawei";
        if (prefix.startsWith("9C3A")) return "Huawei";
        if (prefix.startsWith("E019")) return "Huawei";
        return "未知";
    }
}
