package com.aion.agent;

import android.app.Application;
import com.chaquo.python.android.PyAndroidApplication;

/**
 * Aion Agent —— 手机本地引擎模式。
 * 继承 PyAndroidApplication：Chaquopy 在应用启动时自动初始化 Python 运行时，
 * 使 APK 内嵌的 aion_agent 包（ReAct + 认知记忆 + 本地服务）可直接运行。
 */
public class AionApp extends PyAndroidApplication {
}