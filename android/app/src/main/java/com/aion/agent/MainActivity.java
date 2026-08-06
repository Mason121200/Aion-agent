package com.aion.agent;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * Aion Agent 安卓壳：加载本地/局域网内 Aion 服务的 Web UI。
 * 通过右上角「服务器」按钮修改服务地址（保存后自动重载）。
 */
public class MainActivity extends Activity {

    private static final String PREFS = "aion_prefs";
    private static final String KEY_URL = "server_url";
    private static final String DEFAULT_URL = "http://10.0.2.2:8000";

    private WebView webView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        final String url = prefs.getString(KEY_URL, DEFAULT_URL);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);

        // 顶栏：标题 + 服务器设置
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
        settings.setText("\u2699 服务器");
        settings.setTextColor(Color.WHITE);
        settings.setBackgroundColor(Color.parseColor("#334155"));
        settings.setAllCaps(false);
        settings.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                showSettingsDialog(url);
            }
        });
        bar.addView(settings);
        root.addView(bar, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));

        // WebView
        webView = new WebView(this);
        WebSettings ws = webView.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        ws.setDatabaseEnabled(true);
        webView.setWebViewClient(new WebViewClient());
        root.addView(webView, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));

        setContentView(root);
        webView.loadUrl(url);
    }

    private int dp(int value) {
        return Math.round(getResources().getDisplayMetrics().density * value);
    }

    private void showSettingsDialog(final String currentUrl) {
        final EditText input = new EditText(this);
        input.setText(currentUrl);
        input.setSingleLine(true);
        new AlertDialog.Builder(this)
                .setTitle("服务器地址")
                .setMessage("输入运行 Aion 服务的电脑地址，例如 http://192.168.1.5:8000")
                .setView(input)
                .setPositiveButton("连接", new android.content.DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(android.content.DialogInterface dialog, int which) {
                        String url = input.getText().toString().trim();
                        if (url.isEmpty()) {
                            return;
                        }
                        getSharedPreferences(PREFS, MODE_PRIVATE)
                                .edit().putString(KEY_URL, url).apply();
                        webView.loadUrl(url);
                    }
                })
                .setNegativeButton("取消", null)
                .show();
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
