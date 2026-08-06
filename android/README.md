# Aion Agent Android App

轻量 WebView 壳：加载运行在电脑上的 Aion 本地服务（Web UI），可设置服务器地址。

## 构建 APK

- 本仓库已配置 GitHub Actions：推送 `android/**` 或手动触发
  `Build Android APK` workflow 后，在 Actions 页面的 Artifacts 下载
  `aion-agent-apk`，把里面的 `app-debug.apk` 发给用户直接安装（Android 允许侧载）。
- 也可本地构建：安装 Android Studio 后打开本目录直接 Run。

## 使用

1. 在电脑上启动 Aion 服务：`aion serve`（默认端口 8000）。
2. 手机与电脑连同一 Wi-Fi。
3. 打开 App，点右上角「服务器」，输入电脑局域网 IP：
   `http://192.168.x.x:8000`，点连接。
4. 首次使用建议在浏览器打开该地址后「添加到主屏幕」体验 PWA 全屏模式。

> 模拟器内默认地址为 `http://10.0.2.2:8000`（指向宿主机）。
