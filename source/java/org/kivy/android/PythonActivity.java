package org.kivy.android;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.net.Uri;
import android.os.AsyncTask;
import android.os.Bundle;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.Log;
import android.view.KeyEvent;
import android.view.ViewGroup;
import android.view.ViewGroup.LayoutParams;
import android.webkit.CookieManager;
import android.webkit.WebBackForwardList;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.JsResult;
import android.webkit.JsPromptResult;
import android.webkit.WebChromeClient;
import android.widget.AbsoluteLayout;
import android.widget.ImageView;
import android.widget.Toast;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.lang.reflect.InvocationTargetException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.List;
import android.os.Environment;
import android.content.ContentValues;
import android.provider.MediaStore;
import android.os.ParcelFileDescriptor;
import java.io.FileOutputStream;
import java.io.OutputStream;
import org.renpy.android.ResourceManager;
// [CAILING PATCH] Import WifiBridge for native WiFi scanning
import org.kivy.android.WifiBridge;

public class PythonActivity extends Activity {
    // This activity is modified from a mixture of the SDLActivity and
    // PythonActivity in the SDL2 bootstrap, but removing all the SDL2
    // specifics.

    private static final String TAG = "PythonActivity";

    public static PythonActivity mActivity = null;
    // [CAILING PATCH] WifiBridge instance for JS-native WiFi scanning
    public static WifiBridge mWifiBridge = null;
    // [CAILING PATCH] 文件选择回调
    private static final int REQUEST_FILE_CHOOSER = 10101;
    private android.webkit.ValueCallback<android.net.Uri[]> mFilePathCallback = null;
    public static boolean mOpenExternalLinksInBrowser = false;

    /** If shared libraries (e.g. SDL or the native application) could not be loaded. */
    public static boolean mBrokenLibraries;

    protected static ViewGroup mLayout;
    protected static WebView mWebView;

    protected static Thread mPythonThread;

    private ResourceManager resourceManager = null;
    private Bundle mMetaData = null;
    private PowerManager.WakeLock mWakeLock = null;

    public String getAppRoot() {
        String app_root = getFilesDir().getAbsolutePath() + "/app";
        return app_root;
    }

    public String getEntryPoint(String search_dir) {
        /* Get the main file (.pyc|.py) depending on if we
         * have a compiled version or not.
         */
        List<String> entryPoints = new ArrayList<String>();
        entryPoints.add("main.pyc"); // python 3 compiled files
        for (String value : entryPoints) {
            File mainFile = new File(search_dir + "/" + value);
            if (mainFile.exists()) {
                return value;
            }
        }
        return "main.py";
    }

    public static void initialize() {
        // The static nature of the singleton and Android quirkyness force us to initialize
        // everything here
        // Otherwise, when exiting the app and returning to it, these variables *keep* their pre
        // exit values
        mWebView = null;
        mLayout = null;
        mBrokenLibraries = false;
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        Log.v(TAG, "My oncreate running");
        resourceManager = new ResourceManager(this);
        super.onCreate(savedInstanceState);

        this.mActivity = this;
        this.showLoadingScreen();
        new UnpackFilesTask().execute(getAppRoot());
    }

    private class UnpackFilesTask extends AsyncTask<String, Void, String> {
        @Override
        protected String doInBackground(String... params) {
            File app_root_file = new File(params[0]);
            Log.v(TAG, "Ready to unpack");
            PythonUtil.unpackAsset(mActivity, "private", app_root_file, true);
            PythonUtil.unpackPyBundle(
                    mActivity,
                    getApplicationInfo().nativeLibraryDir + "/" + "libpybundle",
                    app_root_file,
                    false);
            return null;
        }

        @Override
        protected void onPostExecute(String result) {
            Log.v("Python", "Device: " + android.os.Build.DEVICE);
            Log.v("Python", "Model: " + android.os.Build.MODEL);

            PythonActivity.initialize();

            // Load shared libraries
            String errorMsgBrokenLib = "";
            try {
                loadLibraries();
            } catch (UnsatisfiedLinkError e) {
                System.err.println(e.getMessage());
                mBrokenLibraries = true;
                errorMsgBrokenLib = e.getMessage();
            } catch (Exception e) {
                System.err.println(e.getMessage());
                mBrokenLibraries = true;
                errorMsgBrokenLib = e.getMessage();
            }

            if (mBrokenLibraries) {
                AlertDialog.Builder dlgAlert = new AlertDialog.Builder(PythonActivity.mActivity);
                dlgAlert.setMessage(
                        "An error occurred while trying to load the application libraries. Please try again and/or reinstall."
                                + System.getProperty("line.separator")
                                + System.getProperty("line.separator")
                                + "Error: "
                                + errorMsgBrokenLib);
                dlgAlert.setTitle("Python Error");
                dlgAlert.setPositiveButton(
                        "Exit",
                        new DialogInterface.OnClickListener() {
                            @Override
                            public void onClick(DialogInterface dialog, int id) {
                                // if this button is clicked, close current activity
                                PythonActivity.mActivity.finish();
                            }
                        });
                dlgAlert.setCancelable(false);
                dlgAlert.create().show();

                return;
            }

            // Set up the webview
            String app_root_dir = getAppRoot();

            mWebView = new WebView(PythonActivity.mActivity);
            mWebView.getSettings().setJavaScriptEnabled(true);
            mWebView.getSettings().setDomStorageEnabled(true);
            // [CAILING PATCH] Allow local file access for Flask assets
            mWebView.getSettings().setAllowFileAccessFromFileURLs(true);
            mWebView.getSettings().setAllowUniversalAccessFromFileURLs(true);
            // [CAILING PATCH] Create and inject WifiBridge for native WiFi scanning
            mWifiBridge = new WifiBridge(PythonActivity.mActivity);
            mWebView.addJavascriptInterface(mWifiBridge, "WifiBridge");
            mWebView.loadUrl("file:///android_asset/_load.html");

            mWebView.setLayoutParams(
                    new LayoutParams(LayoutParams.FILL_PARENT, LayoutParams.FILL_PARENT));
            mWebView.setWebViewClient(
                    new WebViewClient() {
                        @Override
                        public boolean shouldOverrideUrlLoading(WebView view, String url) {
                            Uri u = Uri.parse(url);
                            if (mOpenExternalLinksInBrowser) {
                                if (!(u.getScheme().equals("file")
                                        || u.getHost().equals("127.0.0.1"))) {
                                    Intent i = new Intent(Intent.ACTION_VIEW, u);
                                    startActivity(i);
                                    return true;
                                }
                            }
                            return false;
                        }

                        @Override
                        public void onPageFinished(WebView view, String url) {
                            CookieManager.getInstance().flush();
                        }
                    });
            // [CAILING PATCH] 设置WebChromeClient以支持confirm/alert/prompt弹窗
            // 没有WebChromeClient，JS的confirm()不工作，导致删除功能无法确认
            mWebView.setWebChromeClient(new WebChromeClient() {
                @Override
                public boolean onJsConfirm(WebView view, String url, String message, JsResult result) {
                    new AlertDialog.Builder(PythonActivity.mActivity)
                        .setMessage(message)
                        .setPositiveButton("确定", (dialog, which) -> result.confirm())
                        .setNegativeButton("取消", (dialog, which) -> result.cancel())
                        .setOnCancelListener(dialog -> result.cancel())
                        .show();
                    return true;
                }
                @Override
                public boolean onJsAlert(WebView view, String url, String message, JsResult result) {
                    new AlertDialog.Builder(PythonActivity.mActivity)
                        .setMessage(message)
                        .setPositiveButton("确定", (dialog, which) -> result.confirm())
                        .setOnCancelListener(dialog -> result.confirm())
                        .show();
                    return true;
                }
                // [CAILING PATCH] 支持 <input type="file"> 文件选择
                @Override
                public boolean onShowFileChooser(WebView webView, android.webkit.ValueCallback<android.net.Uri[]> filePathCallback, FileChooserParams fileChooserParams) {
                    mFilePathCallback = filePathCallback;
                    Intent intent = fileChooserParams.createIntent();
                    try {
                        startActivityForResult(intent, REQUEST_FILE_CHOOSER);
                    } catch (Exception e) {
                        mFilePathCallback = null;
                        return false;
                    }
                    return true;
                }
            });
            // [CAILING PATCH] 支持文件下载（XLSX.writeFile等触发）
            mWebView.setDownloadListener((url, userAgent, contentDisposition, mimetype, contentLength) -> {
                try {
                    // 从URL中提取文件名
                    String filename = android.webkit.URLUtil.guessFileName(url, contentDisposition, mimetype);
                    // 保存到Downloads目录
                    if (android.os.Build.VERSION.SDK_INT >= 29) {
                        ContentValues values = new ContentValues();
                        values.put(MediaStore.Downloads.DISPLAY_NAME, filename);
                        values.put(MediaStore.Downloads.MIME_TYPE, mimetype != null ? mimetype : "application/octet-stream");
                        values.put(MediaStore.Downloads.IS_PENDING, 1);
                        android.net.Uri uri = getContentResolver().insert(MediaStore.Downloads.getContentUri("external_primary"), values);
                        if (uri != null) {
                            OutputStream os = getContentResolver().openOutputStream(uri);
                            java.net.URL downloadUrl = new java.net.URL(url);
                            InputStream is = downloadUrl.openStream();
                            byte[] buffer = new byte[8192];
                            int len;
                            while ((len = is.read(buffer)) > 0) os.write(buffer, 0, len);
                            os.close(); is.close();
                            values.clear(); values.put(MediaStore.Downloads.IS_PENDING, 0);
                            getContentResolver().update(uri, values, null, null);
                            runOnUiThread(() -> Toast.makeText(PythonActivity.mActivity, "已保存到下载目录: " + filename, Toast.LENGTH_LONG).show());
                        }
                    } else {
                        File dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS);
                        File file = new File(dir, filename);
                        java.net.URL downloadUrl = new java.net.URL(url);
                        InputStream is = downloadUrl.openStream();
                        FileOutputStream fos = new FileOutputStream(file);
                        byte[] buffer = new byte[8192];
                        int len;
                        while ((len = is.read(buffer)) > 0) fos.write(buffer, 0, len);
                        fos.close(); is.close();
                        runOnUiThread(() -> Toast.makeText(PythonActivity.mActivity, "已保存到: " + file.getAbsolutePath(), Toast.LENGTH_LONG).show());
                    }
                } catch (Exception e) {
                    Log.e(TAG, "Download error: " + e.getMessage());
                    runOnUiThread(() -> Toast.makeText(PythonActivity.mActivity, "下载失败: " + e.getMessage(), Toast.LENGTH_LONG).show());
                }
            });
            mLayout = new AbsoluteLayout(PythonActivity.mActivity);
            mLayout.addView(mWebView);

            setContentView(mLayout);

            String mFilesDirectory = mActivity.getFilesDir().getAbsolutePath();
            String entry_point = getEntryPoint(app_root_dir);

            Log.v(TAG, "Setting env vars for start.c and Python to use");
            PythonActivity.nativeSetenv("ANDROID_ENTRYPOINT", entry_point);
            PythonActivity.nativeSetenv("ANDROID_ARGUMENT", app_root_dir);
            PythonActivity.nativeSetenv("ANDROID_APP_PATH", app_root_dir);
            PythonActivity.nativeSetenv("ANDROID_PRIVATE", mFilesDirectory);
            PythonActivity.nativeSetenv("ANDROID_UNPACK", app_root_dir);
            PythonActivity.nativeSetenv("PYTHONHOME", app_root_dir);
            PythonActivity.nativeSetenv("PYTHONPATH", app_root_dir + ":" + app_root_dir + "/lib");
            PythonActivity.nativeSetenv("PYTHONOPTIMIZE", "2");

            try {
                Log.v(TAG, "Access to our meta-data...");
                mActivity.mMetaData =
                        mActivity
                                .getPackageManager()
                                .getApplicationInfo(
                                        mActivity.getPackageName(), PackageManager.GET_META_DATA)
                                .metaData;

                PowerManager pm = (PowerManager) mActivity.getSystemService(Context.POWER_SERVICE);
                if (mActivity.mMetaData.getInt("wakelock") == 1) {
                    mActivity.mWakeLock =
                            pm.newWakeLock(PowerManager.SCREEN_BRIGHT_WAKE_LOCK, "Screen On");
                    mActivity.mWakeLock.acquire();
                }
            } catch (PackageManager.NameNotFoundException e) {
            }

            final Thread pythonThread = new Thread(new PythonMain(), "PythonThread");
            PythonActivity.mPythonThread = pythonThread;
            pythonThread.start();

            final Thread wvThread = new Thread(new WebViewLoaderMain(), "WvThread");
            wvThread.start();
        }
    }

    @Override
    public void onDestroy() {
        Log.i("Destroy", "end of app");
        super.onDestroy();

        // make sure all child threads (python_thread) are stopped
        android.os.Process.killProcess(android.os.Process.myPid());
    }

    public void loadLibraries() {
        String app_root = new String(getAppRoot());
        File app_root_file = new File(app_root);
        PythonUtil.loadLibraries(app_root_file, new File(getApplicationInfo().nativeLibraryDir));
    }

    public static void loadUrl(String url) {
        class LoadUrl implements Runnable {
            private String mUrl;

            public LoadUrl(String url) {
                mUrl = url;
            }

            public void run() {
                mWebView.loadUrl(mUrl);
            }
        }

        Log.i(TAG, "Opening URL: " + url);
        mActivity.runOnUiThread(new LoadUrl(url));
    }

    public static void enableZoom() {
        mActivity.runOnUiThread(
                new Runnable() {
                    @Override
                    public void run() {
                        mWebView.getSettings().setBuiltInZoomControls(true);
                        mWebView.getSettings().setDisplayZoomControls(false);
                    }
                });
    }

    public static ViewGroup getLayout() {
        return mLayout;
    }

    long lastBackClick = 0;

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        // Check if the key event was the Back button
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            // Go back if there is web page history behind,
            // but not to the start preloader
            WebBackForwardList webViewBackForwardList = mWebView.copyBackForwardList();
            if (webViewBackForwardList.getCurrentIndex() > 1) {
                mWebView.goBack();
                return true;
            }

            // If there's no web page history, bubble up to the default
            // system behavior (probably exit the activity)
            if (SystemClock.elapsedRealtime() - lastBackClick > 2000) {
                lastBackClick = SystemClock.elapsedRealtime();
                Toast.makeText(this, "Tap again to close the app", Toast.LENGTH_LONG).show();
                return true;
            }

            lastBackClick = SystemClock.elapsedRealtime();
        }

        return super.onKeyDown(keyCode, event);
    }

    // loading screen implementation
    public static ImageView mImageView = null;

    public void removeLoadingScreen() {
        runOnUiThread(
                new Runnable() {
                    public void run() {
                        if (PythonActivity.mImageView != null
                                && PythonActivity.mImageView.getParent() != null) {
                            ((ViewGroup) PythonActivity.mImageView.getParent())
                                    .removeView(PythonActivity.mImageView);
                            PythonActivity.mImageView = null;
                        }
                    }
                });
    }

    protected void showLoadingScreen() {
        // load the bitmap
        // 1. if the image is valid and we don't have layout yet, assign this bitmap
        // as main view.
        // 2. if we have a layout, just set it in the layout.
        // 3. If we have an mImageView already, then do nothing because it will have
        // already been made the content view or added to the layout.

        if (mImageView == null) {
            int presplashId = this.resourceManager.getIdentifier("presplash", "drawable");
            InputStream is = this.getResources().openRawResource(presplashId);
            Bitmap bitmap = null;
            try {
                bitmap = BitmapFactory.decodeStream(is);
            } finally {
                try {
                    is.close();
                } catch (IOException e) {
                }
                ;
            }

            mImageView = new ImageView(this);
            mImageView.setImageBitmap(bitmap);

            String backgroundColor = resourceManager.getString("presplash_color");
            if (backgroundColor != null) {
                try {
                    mImageView.setBackgroundColor(Color.parseColor(backgroundColor));
                } catch (IllegalArgumentException e) {
                }
            }
            mImageView.setLayoutParams(
                    new ViewGroup.LayoutParams(
                            ViewGroup.LayoutParams.FILL_PARENT,
                            ViewGroup.LayoutParams.FILL_PARENT));
            mImageView.setScaleType(ImageView.ScaleType.FIT_CENTER);
        }

        if (mLayout == null) {
            setContentView(mImageView);
        } else if (PythonActivity.mImageView.getParent() == null) {
            mLayout.addView(mImageView);
        }
    }

    // ----------------------------------------------------------------------------
    // Listener interface for onNewIntent
    //

    public interface NewIntentListener {
        void onNewIntent(Intent intent);
    }

    private List<NewIntentListener> newIntentListeners = null;

    public void registerNewIntentListener(NewIntentListener listener) {
        if (this.newIntentListeners == null)
            this.newIntentListeners =
                    Collections.synchronizedList(new ArrayList<NewIntentListener>());
        this.newIntentListeners.add(listener);
    }

    public void unregisterNewIntentListener(NewIntentListener listener) {
        if (this.newIntentListeners == null) return;
        this.newIntentListeners.remove(listener);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        if (this.newIntentListeners == null) return;
        this.onResume();
        synchronized (this.newIntentListeners) {
            Iterator<NewIntentListener> iterator = this.newIntentListeners.iterator();
            while (iterator.hasNext()) {
                (iterator.next()).onNewIntent(intent);
            }
        }
    }

    // ----------------------------------------------------------------------------
    // Listener interface for onActivityResult
    //

    public interface ActivityResultListener {
        void onActivityResult(int requestCode, int resultCode, Intent data);
    }

    private List<ActivityResultListener> activityResultListeners = null;

    public void registerActivityResultListener(ActivityResultListener listener) {
        if (this.activityResultListeners == null)
            this.activityResultListeners =
                    Collections.synchronizedList(new ArrayList<ActivityResultListener>());
        this.activityResultListeners.add(listener);
    }

    public void unregisterActivityResultListener(ActivityResultListener listener) {
        if (this.activityResultListeners == null) return;
        this.activityResultListeners.remove(listener);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent intent) {
        // [CAILING PATCH] 处理文件选择结果
        if (requestCode == REQUEST_FILE_CHOOSER && mFilePathCallback != null) {
            android.net.Uri[] results = null;
            if (resultCode == RESULT_OK && intent != null) {
                android.net.Uri data = intent.getData();
                if (data != null) {
                    results = new android.net.Uri[]{data};
                } else if (intent.getClipData() != null) {
                    int count = intent.getClipData().getItemCount();
                    results = new android.net.Uri[count];
                    for (int i = 0; i < count; i++) {
                        results[i] = intent.getClipData().getItemAt(i).getUri();
                    }
                }
            }
            mFilePathCallback.onReceiveValue(results);
            mFilePathCallback = null;
            return;
        }
        if (this.activityResultListeners == null) return;
        this.onResume();
        synchronized (this.activityResultListeners) {
            Iterator<ActivityResultListener> iterator = this.activityResultListeners.iterator();
            while (iterator.hasNext())
                (iterator.next()).onActivityResult(requestCode, resultCode, intent);
        }
    }

    public static void start_service(
            String serviceTitle, String serviceDescription, String pythonServiceArgument) {
        _do_start_service(serviceTitle, serviceDescription, pythonServiceArgument, true);
    }

    public static void start_service_not_as_foreground(
            String serviceTitle, String serviceDescription, String pythonServiceArgument) {
        _do_start_service(serviceTitle, serviceDescription, pythonServiceArgument, false);
    }

    public static void _do_start_service(
            String serviceTitle,
            String serviceDescription,
            String pythonServiceArgument,
            boolean showForegroundNotification) {
        Intent serviceIntent = new Intent(PythonActivity.mActivity, PythonService.class);
        String argument = PythonActivity.mActivity.getFilesDir().getAbsolutePath();
        String app_root_dir = PythonActivity.mActivity.getAppRoot();
        String entry_point = PythonActivity.mActivity.getEntryPoint(app_root_dir + "/service");
        serviceIntent.putExtra("androidPrivate", argument);
        serviceIntent.putExtra("androidArgument", app_root_dir);
        serviceIntent.putExtra("serviceEntrypoint", "service/" + entry_point);
        serviceIntent.putExtra("pythonName", "python");
        serviceIntent.putExtra("pythonHome", app_root_dir);
        serviceIntent.putExtra("pythonPath", app_root_dir + ":" + app_root_dir + "/lib");
        serviceIntent.putExtra(
                "serviceStartAsForeground", (showForegroundNotification ? "true" : "false"));
        serviceIntent.putExtra("serviceTitle", serviceTitle);
        serviceIntent.putExtra("serviceDescription", serviceDescription);
        serviceIntent.putExtra("pythonServiceArgument", pythonServiceArgument);
        PythonActivity.mActivity.startService(serviceIntent);
    }

    public static void stop_service() {
        Intent serviceIntent = new Intent(PythonActivity.mActivity, PythonService.class);
        PythonActivity.mActivity.stopService(serviceIntent);
    }

    public static native void nativeSetenv(String name, String value);

    public static native int nativeInit(Object arguments);

    /**
     * Used by android.permissions p4a module to register a call back after requesting runtime
     * permissions
     */
    public interface PermissionsCallback {
        void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults);
    }

    private PermissionsCallback permissionCallback;
    private boolean havePermissionsCallback = false;

    public void addPermissionsCallback(PermissionsCallback callback) {
        permissionCallback = callback;
        havePermissionsCallback = true;
        Log.v(TAG, "addPermissionsCallback(): Added callback for onRequestPermissionsResult");
    }

    @Override
    public void onRequestPermissionsResult(
            int requestCode, String[] permissions, int[] grantResults) {
        Log.v(TAG, "onRequestPermissionsResult()");
        if (havePermissionsCallback) {
            Log.v(TAG, "onRequestPermissionsResult passed to callback");
            permissionCallback.onRequestPermissionsResult(requestCode, permissions, grantResults);
        }
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
    }

    /** Used by android.permissions p4a module to check a permission */
    public boolean checkCurrentPermission(String permission) {
        if (android.os.Build.VERSION.SDK_INT < 23) return true;

        try {
            java.lang.reflect.Method methodCheckPermission =
                    Activity.class.getMethod("checkSelfPermission", String.class);
            Object resultObj = methodCheckPermission.invoke(this, permission);
            int result = Integer.parseInt(resultObj.toString());
            if (result == PackageManager.PERMISSION_GRANTED) return true;
        } catch (IllegalAccessException | NoSuchMethodException | InvocationTargetException e) {
        }
        return false;
    }

    /** Used by android.permissions p4a module to request runtime permissions */
    public void requestPermissionsWithRequestCode(String[] permissions, int requestCode) {
        if (android.os.Build.VERSION.SDK_INT < 23) return;
        try {
            java.lang.reflect.Method methodRequestPermission =
                    Activity.class.getMethod("requestPermissions", String[].class, int.class);
            methodRequestPermission.invoke(this, permissions, requestCode);
        } catch (IllegalAccessException | NoSuchMethodException | InvocationTargetException e) {
        }
    }

    public void requestPermissions(String[] permissions) {
        requestPermissionsWithRequestCode(permissions, 1);
    }
}

class PythonMain implements Runnable {
    @Override
    public void run() {
        PythonActivity.nativeInit(new String[0]);
    }
}

class WebViewLoaderMain implements Runnable {
    @Override
    public void run() {
        WebViewLoader.testConnection();
    }
}
