package com.aion.agent;

import android.app.Activity;
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
        WebSettings ws = webView.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        ws.setDatabaseEnabled(true);
        webView.setWebViewClient(new WebViewClient());
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

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
