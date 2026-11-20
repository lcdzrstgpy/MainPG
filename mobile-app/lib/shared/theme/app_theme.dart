import 'package:flutter/material.dart';

import 'app_tokens.dart';

/// 全局主题（仅浅色），色值全部来自 [AppTokens]。
///
/// 基调：清爽浅色 —— 极浅灰底 + 纯白卡片 + 品牌蓝点缀。
class AppTheme {
  AppTheme._();

  /// 兼容旧引用：品牌色
  static const Color brand = AppTokens.brand;

  static ThemeData get light {
    const ColorScheme scheme = ColorScheme.light(
      primary: AppTokens.brand,
      onPrimary: Colors.white,
      primaryContainer: AppTokens.brandWeak,
      onPrimaryContainer: AppTokens.brand600,
      secondary: AppTokens.violet,
      onSecondary: Colors.white,
      secondaryContainer: AppTokens.violetWeak,
      onSecondaryContainer: AppTokens.violet,
      surface: AppTokens.surface,
      onSurface: AppTokens.t1,
      surfaceContainerLowest: AppTokens.surface,
      surfaceContainerLow: AppTokens.sunken,
      surfaceContainer: AppTokens.bg,
      outlineVariant: AppTokens.cardBorder,
      outline: AppTokens.line2,
      error: AppTokens.danger,
      onError: Colors.white,
    );

    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: AppTokens.bg,
      splashFactory: InkRipple.splashFactory,
      // 按压水波纹更轻，避免浅色面上显脏
      splashColor: AppTokens.brand.withValues(alpha: 0.06),
      highlightColor: AppTokens.brand.withValues(alpha: 0.04),
      fontFamilyFallback: const <String>[
        'PingFang SC',
        'HarmonyOS Sans SC',
        'Microsoft YaHei',
        'Noto Sans SC',
      ],
      textTheme: const TextTheme(
        bodyMedium: TextStyle(fontSize: 14, color: AppTokens.t1, height: 1.5),
      ),
      dividerColor: AppTokens.line,
      appBarTheme: const AppBarTheme(
        backgroundColor: AppTokens.bg,
        surfaceTintColor: Colors.transparent,
        foregroundColor: AppTokens.t1,
        elevation: 0,
        centerTitle: true,
      ),
      // 底部导航样式在 AppShell 的 _DockBar 内显式指定，此处不再覆盖
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(foregroundColor: AppTokens.brand),
      ),
    );
  }
}
