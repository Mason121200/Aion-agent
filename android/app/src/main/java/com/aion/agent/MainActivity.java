package com.aion.agent;

import android.app.Activity;
import android.Manifest;
import android.content.ContentValues;
import android.content.Intent;
import android.net.Uri;
import android.os.Environment;
import android.provider.MediaStore;
import android.app.AlertDialog;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.webkit.WebSettings;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.JsResult;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.util.Log;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import com.chaquo.python.Python;
import com.chaquo.python.PyObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * Aion Agent —— 真正的独立 App：
 * APK 内嵌 Python 引擎（aion_agent 包），App 启动即在手机本地运行
 * 本地服务（127.0.0.1:8000），WebView 加载本地 Web UI；
 * 认知记忆 / 会话历史 / API Key 全部保存在 App 私有目录（数据不上传）。
 */
public class MainActivity extends Activity {

    private static final String PREFS = "aion_prefs";
    private static final String KEY_API_KEY = "api_key";
    private static final String KEY_AGNES_KEY = "agnes_api_key";
    private static final String KEY_AGNES_URL = "agnes_base_url";
    private static final String KEY_AGNES_IMAGE_MODEL = "agnes_image_model";
    private static final String KEY_AGNES_VIDEO_MODEL = "agnes_video_model";
    private static final String KEY_URL = "server_url";
    private static final String LOCAL_URL = "http://127.0.0.1:8000";

    private WebView webView;
    private TextView statusText;
    private SharedPreferences prefs;
    private volatile boolean engineReady = false;
    private volatile String lastHealthError = "";
    private static final int REQ_FILE_CHOOSER = 1001;
    private static final int REQ_WRITE_STORAGE = 1002;
    private ValueCallback<Uri[]> uploadCallback;
    private volatile String pendingSaveUrl;
    private volatile String pendingSaveName;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        createNotificationChannel();
        requestNotificationPermission();

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);

        // 顶栏：标题 + 设置
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(Color.parseColor("#0f172a"));
        bar.setPadding(dp(14), dp(10), dp(10), dp(10));

        TextView title = new TextView(this);
        title.setText("Aion Agent");
        title.setTextColor(Color.WHITE);
        title.setTextSize(17);
        bar.addView(title, new LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));

        Button settings = new Button(this);
        settings.setText("\u2699 设置");
        settings.setTextColor(Color.WHITE);
        settings.setBackgroundColor(Color.parseColor("#334155"));
        settings.setAllCaps(false);
        settings.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                showSettingsDialog();
            }
        });
        bar.addView(settings);
        root.addView(bar, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));

        // 启动状态
        statusText = new TextView(this);
        statusText.setText("\u23f3 正在启动本地引擎…");
        statusText.setTextColor(Color.parseColor("#64748b"));
        statusText.setTextSize(13);
        statusText.setPadding(dp(14), dp(10), dp(14), dp(6));
        root.addView(statusText);

        // WebView
        webView = new WebView(this);
        WebView.setWebContentsDebuggingEnabled(true);
        WebSettings ws = webView.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        ws.setDatabaseEnabled(true);
        webView.setWebViewClient(new WebViewClient());

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onJsAlert(WebView view, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                        .setMessage(message)
                        .setPositiveButton("\u786e\u5b9a", new android.content.DialogInterface.OnClickListener() {
                            @Override
                            public void onClick(android.content.DialogInterface dialog, int which) {
                                result.confirm();
                            }
                        })
                        .setCancelable(false)
                        .show();
                return true;
            }

            @Override
            public boolean onJsConfirm(WebView view, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                        .setMessage(message)
                        .setPositiveButton("\u786e\u5b9a", new android.content.DialogInterface.OnClickListener() {
                            @Override
                            public void onClick(android.content.DialogInterface dialog, int which) {
                                result.confirm();
                            }
                        })
                        .setNegativeButton("\u53d6\u6d88", new android.content.DialogInterface.OnClickListener() {
                            @Override
                            public void onClick(android.content.DialogInterface dialog, int which) {
                                result.cancel();
                            }
                        })
                        .setCancelable(false)
                        .show();
                return true;
            }

            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> filePathCallback,
                                             WebChromeClient.FileChooserParams fileChooserParams) {
                if (uploadCallback != null) {
                    uploadCallback.onReceiveValue(null);
                }
                uploadCallback = filePathCallback;
                Intent intent = fileChooserParams.createIntent();
                intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                try {
                    startActivityForResult(intent, REQ_FILE_CHOOSER);
                } catch (Exception e) {
                    uploadCallback = null;
                    return false;
                }
                return true;
            }
            @Override
            public boolean onConsoleMessage(android.webkit.ConsoleMessage consoleMessage) {
                Log.d("AionWeb", consoleMessage.message()
                        + " [" + consoleMessage.sourceId() + ":" + consoleMessage.lineNumber() + "]");
                return true;
            }
        });
        webView.setOnLongClickListener(new View.OnLongClickListener() {
            @Override
            public boolean onLongClick(View v) {
                WebView.HitTestResult hr = webView.getHitTestResult();
                int type = hr == null ? 0 : hr.getType();
                if (type == WebView.HitTestResult.IMAGE_TYPE
                        || type == WebView.HitTestResult.SRC_IMAGE_ANCHOR_TYPE) {
                    final String imgUrl = hr.getExtra();
                    if (imgUrl != null) {
                        new AlertDialog.Builder(MainActivity.this)
                                .setTitle("\u56fe\u7247\u64cd\u4f5c")
                                .setItems(new String[]{"\u4fdd\u5b58\u56fe\u7247\u5230\u76f8\u518c"}, new android.content.DialogInterface.OnClickListener() {
                                    @Override
                                    public void onClick(android.content.DialogInterface dialog, int which) {
                                        saveImageToPhoneAsync(imgUrl, guessImageName(imgUrl));
                                    }
                                })
                                .show();
                        return true;
                    }
                }
                return false;
            }
        });

        webView.addJavascriptInterface(new Object() {
            @JavascriptInterface
            public void openSettings() {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        showSettingsDialog();
                    }
                });
            }

            @JavascriptInterface
            public void notify(final String title, final String body) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        showSystemNotification(title, body);
                    }
                });
            }

            @JavascriptInterface
            public void saveImage(final String url, final String name) {
                saveImageToPhoneAsync(url, name);
            }
        }, "AionAndroid");
        root.addView(webView, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));

        setContentView(root);

        startLocalEngine();
    }

    /** 后台启动内嵌 Python 引擎，就绪后加载本地 Web UI */
    private void startLocalEngine() {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    String dataDir = getFilesDir().getAbsolutePath();
                    // 1. 已保存的 API Key 先写入数据目录 .env
                    String key = prefs.getString(KEY_API_KEY, "");
                    if (!key.isEmpty()) {
                        writeEnvFile(dataDir, key);
                    }
                    // 2. 启动本地服务（127.0.0.1:8000，数据存 App 私有目录）
                    Python py = Python.getInstance();
                    PyObject mod = py.getModule("aion_agent.server.local_server");
                    mod.callAttr("start_local_server", "127.0.0.1", 8000, dataDir);
                    // 3. 轮询健康检查，等待就绪
                    boolean ok = false;
                    for (int i = 0; i < 60; i++) {
                        Thread.sleep(250);
                        if (healthOk()) {
                            ok = true;
                            break;
                        }
                    }
                    final boolean ready = ok;
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            if (ready) {
                                engineReady = true;
                                statusText.setText("本地引擎已就绪（数据保存在本机）");
                                webView.loadUrl(LOCAL_URL);
                            } else {
                                statusText.setText("本地引擎启动超时: " + lastHealthError + "（请重启应用）");
                            }
                        }
                    });
                } catch (final Exception e) {
                    Log.e("AionAgent", "startLocalEngine failed", e);
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            statusText.setText("启动异常: " + e);
                        }
                    });
                }
            }
        }).start();
    }

    private boolean healthOk() {
        try {
            HttpURLConnection conn = (HttpURLConnection)
                    new URL(LOCAL_URL + "/api/health").openConnection();
            conn.setConnectTimeout(1500);
            conn.setReadTimeout(1500);
            int code = conn.getResponseCode();
            conn.disconnect();
            return code == 200;
        } catch (Exception e) {
            lastHealthError = String.valueOf(e.getMessage());
            return false;
        }
    }

    /** 通知渠道 + 运行时权限（Android 13+ 需要 POST_NOTIFICATIONS） */
    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(
                    "aion_reminder", "Aion 提醒", NotificationManager.IMPORTANCE_HIGH);
            ch.setDescription("学习计划与闹钟提醒");
            getSystemService(NotificationManager.class).createNotificationChannel(ch);
        }
    }

    private void requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(
                    new String[]{android.Manifest.permission.POST_NOTIFICATIONS}, 1);
        }
    }

    /** 系统通知（WebView 前端通过 AionAndroid.notify 调用） */
    private void showSystemNotification(String title, String body) {
        try {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            String channelId = "aion_reminder";
            if (Build.VERSION.SDK_INT >= 26) {
                NotificationChannel ch = new NotificationChannel(
                        channelId, "Aion 提醒", NotificationManager.IMPORTANCE_HIGH);
                ch.setDescription("学习计划与闹钟提醒");
                nm.createNotificationChannel(ch);
            }
            Notification.Builder builder = Build.VERSION.SDK_INT >= 26
                    ? new Notification.Builder(this, channelId)
                    : new Notification.Builder(this);
            builder.setSmallIcon(android.R.drawable.ic_dialog_info)
                    .setContentTitle(title)
                    .setContentText(body)
                    .setAutoCancel(true)
                    .setDefaults(Notification.DEFAULT_ALL);
            nm.notify((int) System.currentTimeMillis(), builder.build());
        } catch (Exception e) {
            Log.e("AionAgent", "showSystemNotification failed", e);
        }
    }

    /** 把 API Key 写入数据目录 .env（保留已有配置，如 AGNES_*） */
    private void writeEnvFile(String dataDir, String key) {
        try {
            File env = new File(dataDir, ".env");
            StringBuilder sb = new StringBuilder();
            if (env.exists()) {
                java.io.BufferedReader reader = new java.io.BufferedReader(
                        new java.io.InputStreamReader(
                                new java.io.FileInputStream(env), StandardCharsets.UTF_8));
                String line;
                while ((line = reader.readLine()) != null) {
                    String s = line.trim();
                    if (s.startsWith("AION_LLM_API_KEY=") || s.startsWith("LLM_API_KEY=")) {
                        continue;
                    }
                    sb.append(line).append("\n");
                }
                reader.close();
            }
            sb.append("AION_LLM_API_KEY=").append(key.trim()).append("\n");
            FileOutputStream fos = new FileOutputStream(env);
            fos.write(sb.toString().getBytes(StandardCharsets.UTF_8));
            fos.close();
        } catch (Exception ignored) {
        }
    }

    /** 设置页：API Key（必填，DeepSeek）+ 服务器地址（高级） */
    private void showSettingsDialog() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(20), dp(8), dp(20), dp(8));

        TextView keyLabel = new TextView(this);
        keyLabel.setText("API Key（必填，DeepSeek）");
        keyLabel.setTextColor(Color.parseColor("#334155"));
        box.addView(keyLabel);

        final EditText keyInput = new EditText(this);
        keyInput.setHint("sk-...");
        keyInput.setText(prefs.getString(KEY_API_KEY, ""));
        keyInput.setSingleLine(true);
        box.addView(keyInput);

        TextView keyHint = new TextView(this);
        keyHint.setText("在 platform.deepseek.com 创建 API Key（sk- 开头）；保存在手机本地，重启不丢失。");
        keyHint.setTextColor(Color.parseColor("#94a3b8"));
        keyHint.setTextSize(12);
        keyHint.setPadding(dp(4), dp(2), dp(4), dp(8));
        box.addView(keyHint);

        TextView urlLabel = new TextView(this);
        urlLabel.setText("服务器地址（一般保持默认即可）");
        urlLabel.setTextColor(Color.parseColor("#334155"));
        box.addView(urlLabel);

        final EditText urlInput = new EditText(this);
        urlInput.setText(prefs.getString(KEY_URL, LOCAL_URL));
        urlInput.setSingleLine(true);
        box.addView(urlInput);
        TextView agnesLabel = new TextView(this);
        agnesLabel.setText("AGNES API Key（生图/视频用，可选）");
        agnesLabel.setTextColor(Color.parseColor("#334155"));
        box.addView(agnesLabel);

        final EditText agnesKeyInput = new EditText(this);
        agnesKeyInput.setHint("在 Agnes 平台创建");
        agnesKeyInput.setText(prefs.getString(KEY_AGNES_KEY, ""));
        agnesKeyInput.setSingleLine(true);
        box.addView(agnesKeyInput);

        TextView agnesHint = new TextView(this);
        agnesHint.setText("不填则无法使用生图/视频；保存在手机本地，重启不丢失。");
        agnesHint.setTextColor(Color.parseColor("#94a3b8"));
        agnesHint.setTextSize(12);
        agnesHint.setPadding(dp(4), dp(2), dp(4), dp(8));
        box.addView(agnesHint);

        TextView agnesUrlLabel = new TextView(this);
        agnesUrlLabel.setText("AGNES Base URL（高级，默认即可）");
        agnesUrlLabel.setTextColor(Color.parseColor("#334155"));
        box.addView(agnesUrlLabel);

        final EditText agnesUrlInput = new EditText(this);
        agnesUrlInput.setText(prefs.getString(KEY_AGNES_URL, "https://apihub.agnes-ai.com/v1"));
        agnesUrlInput.setSingleLine(true);
        box.addView(agnesUrlInput);

        TextView agnesModelLabel = new TextView(this);
        agnesModelLabel.setText("图片 / 视频模型（高级，默认即可）");
        agnesModelLabel.setTextColor(Color.parseColor("#334155"));
        box.addView(agnesModelLabel);

        final EditText agnesImgInput = new EditText(this);
        agnesImgInput.setHint("图片模型");
        agnesImgInput.setText(prefs.getString(KEY_AGNES_IMAGE_MODEL, "agnes-image-2.1-flash"));
        agnesImgInput.setSingleLine(true);
        box.addView(agnesImgInput);

        final EditText agnesVidInput = new EditText(this);
        agnesVidInput.setHint("视频模型");
        agnesVidInput.setText(prefs.getString(KEY_AGNES_VIDEO_MODEL, "agnes-video-v2.0"));
        agnesVidInput.setSingleLine(true);
        box.addView(agnesVidInput);

        new AlertDialog.Builder(this)
                .setTitle("设置")
                .setView(box)
                .setPositiveButton("保存", new android.content.DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(android.content.DialogInterface dialog, int which) {
                        String key = keyInput.getText().toString().trim();
                        String url = urlInput.getText().toString().trim();
                        if (url.isEmpty()) {
                            url = LOCAL_URL;
                        }
                        String agnesKey = agnesKeyInput.getText().toString().trim();
                        String agnesUrl = agnesUrlInput.getText().toString().trim();
                        if (agnesUrl.isEmpty()) {
                            agnesUrl = "https://apihub.agnes-ai.com/v1";
                        }
                        String agnesImgModel = agnesImgInput.getText().toString().trim();
                        String agnesVidModel = agnesVidInput.getText().toString().trim();
                        prefs.edit()
                                .putString(KEY_API_KEY, key)
                                .putString(KEY_URL, url)
                                .putString(KEY_AGNES_KEY, agnesKey)
                                .putString(KEY_AGNES_URL, agnesUrl)
                                .putString(KEY_AGNES_IMAGE_MODEL, agnesImgModel)
                                .putString(KEY_AGNES_VIDEO_MODEL, agnesVidModel)
                                .apply();
                        // 运行时立即生效：写入 .env 并重置 LLM 缓存（空 key = 清除）
                        try {
                            PyObject mod = Python.getInstance()
                                    .getModule("aion_agent.server.local_server");
                            mod.callAttr("set_api_key", key);
                        } catch (Exception ignored) {
                        }
                        if (key.isEmpty()) {
                            File env = new File(getFilesDir().getAbsolutePath(), ".env");
                            if (env.exists()) env.delete();
                            Toast.makeText(MainActivity.this,
                                    "已清除 API Key", Toast.LENGTH_SHORT).show();
                        } else {
                            writeEnvFile(getFilesDir().getAbsolutePath(), key);
                            Toast.makeText(MainActivity.this,
                                    "API Key 已保存（保存在本机）", Toast.LENGTH_SHORT).show();
                        }
                        try {
                            PyObject agnesMod = Python.getInstance()
                                    .getModule("aion_agent.server.local_server");
                            agnesMod.callAttr("set_agnes_config", agnesKey,
                                    agnesUrl, agnesImgModel, agnesVidModel);
                        } catch (Exception ignored) {
                        }
                        webView.loadUrl(url);
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private int dp(int value) {
        return Math.round(getResources().getDisplayMetrics().density * value);
    }


    /** \u5c06\u804a\u5929\u4e2d\u7684\u56fe\u7247\u4fdd\u5b58\u5230\u624b\u673a\uff08\u76f8\u518c/\u4e0b\u8f7d\u76ee\u5f55\uff09 */
    private void saveImageToPhoneAsync(final String url, final String name) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                final String msg = saveImageToPhone(url, name);
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        Toast.makeText(MainActivity.this, msg, Toast.LENGTH_LONG).show();
                    }
                });
            }
        }).start();
    }

    private static String guessImageName(String url) {
        String clean = url == null ? "" : url.split("[?#]")[0];
        String name = clean.substring(clean.lastIndexOf('/') + 1);
        if (name.isEmpty() || !name.contains(".")) {
            name = "aion_" + Math.abs((url == null ? "" : url).hashCode()) + ".png";
        }
        return name;
    }

    private String saveImageToPhone(String url, String name) {
        if (url == null || !(url.startsWith("http://") || url.startsWith("https://"))) {
            return "\u65e0\u6548\u7684\u56fe\u7247\u5730\u5740";
        }
        String safeName = sanitizeName(name);
        if (Build.VERSION.SDK_INT >= 29) {
            return writeToMediaStore(url, safeName);
        }
        if (checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE)
                != PackageManager.PERMISSION_GRANTED) {
            pendingSaveUrl = url;
            pendingSaveName = safeName;
            requestPermissions(
                    new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE},
                    REQ_WRITE_STORAGE);
            return "\u9700\u8981\u5b58\u50a8\u6743\u9650\uff0c\u8bf7\u5728\u5f39\u51fa\u7684\u6388\u6743\u6846\u4e2d\u5141\u8bb8";
        }
        return writeToLegacyDownload(url, safeName);
    }

    private String writeToMediaStore(String url, String name) {
        ContentValues values = new ContentValues();
        values.put(MediaStore.Downloads.DISPLAY_NAME, name);
        values.put(MediaStore.Downloads.MIME_TYPE, mimeFor(name));
        values.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/AionAgent");
        Uri uri = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
        if (uri == null) {
            return "\u4fdd\u5b58\u5931\u8d25\uff1a\u65e0\u6cd5\u521b\u5efa\u6587\u4ef6";
        }
        try {
            InputStream in = openStream(url);
            OutputStream out = getContentResolver().openOutputStream(uri);
            if (in == null || out == null) {
                throw new java.io.IOException("\u65e0\u6cd5\u6253\u5f00\u8f93\u5165/\u8f93\u51fa\u6d41");
            }
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
            }
            in.close();
            out.close();
            return "\u5df2\u4fdd\u5b58\u5230 \u4e0b\u8f7d/AionAgent/" + name;
        } catch (Exception e) {
            getContentResolver().delete(uri, null, null);
            return "\u4fdd\u5b58\u5931\u8d25\uff1a" + e.getMessage();
        }
    }

    private String writeToLegacyDownload(String url, String name) {
        File dir = new File(
                Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
                "AionAgent");
        if (!dir.exists() && !dir.mkdirs()) {
            return "\u4fdd\u5b58\u5931\u8d25\uff1a\u65e0\u6cd5\u521b\u5efa\u76ee\u5f55";
        }
        File target = new File(dir, name);
        try {
            InputStream in = openStream(url);
            FileOutputStream out = new FileOutputStream(target);
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
            }
            in.close();
            out.close();
            return "\u5df2\u4fdd\u5b58\u5230 \u4e0b\u8f7d/AionAgent/" + name;
        } catch (Exception e) {
            return "\u4fdd\u5b58\u5931\u8d25\uff1a" + e.getMessage();
        }
    }

    private InputStream openStream(String url) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(30000);
        conn.setInstanceFollowRedirects(true);
        return conn.getInputStream();
    }

    private static String sanitizeName(String name) {
        String n = name == null ? "" : name.trim();
        if (n.isEmpty()) {
            n = "aion_image";
        }
        n = n.replaceAll("[/:*?\"<>|]", "_");
        if (n.indexOf('.') < 0) {
            n = n + ".png";
        }
        return n;
    }

    private static String mimeFor(String name) {
        String lower = name == null ? "" : name.toLowerCase();
        if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) {
            return "image/jpeg";
        }
        if (lower.endsWith(".gif")) {
            return "image/gif";
        }
        if (lower.endsWith(".webp")) {
            return "image/webp";
        }
        if (lower.endsWith(".bmp")) {
            return "image/bmp";
        }
        return "image/png";
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQ_FILE_CHOOSER && uploadCallback != null) {
            uploadCallback.onReceiveValue(
                    WebChromeClient.FileChooserParams.parseResult(resultCode, data));
            uploadCallback = null;
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != REQ_WRITE_STORAGE) {
            return;
        }
        boolean granted = grantResults.length > 0
                && grantResults[0] == PackageManager.PERMISSION_GRANTED;
        final String url = pendingSaveUrl;
        final String name = pendingSaveName;
        pendingSaveUrl = null;
        pendingSaveName = null;
        if (!granted || url == null) {
            Toast.makeText(this, "\u672a\u6388\u4e88\u5b58\u50a8\u6743\u9650\uff0c\u65e0\u6cd5\u4fdd\u5b58\u56fe\u7247", Toast.LENGTH_SHORT).show();
            return;
        }
        new Thread(new Runnable() {
            @Override
            public void run() {
                final String msg = writeToLegacyDownload(url, name);
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        Toast.makeText(MainActivity.this, msg, Toast.LENGTH_LONG).show();
                    }
                });
            }
        }).start();
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
