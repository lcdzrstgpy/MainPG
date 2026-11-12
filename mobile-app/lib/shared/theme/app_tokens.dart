import 'package:flutter/material.dart';

/// 设计令牌 —— 与 `mobile-prototype/styles.css` 的 `:root` 一一对应。
///
/// 视觉基调：**清爽浅色 + 彩色点缀**（类国内主流工具类 App / XUI）。
/// 大面积留白 + 极浅灰底 + 白色卡片，功能入口用低饱和彩色图标块点缀。
/// 仅浅色：拍照页为「深色页面」而非深色主题，通过 [cameraBg] 单独表达。
class AppTokens {
  AppTokens._();

  // ---------- 品牌色 ----------
  static const Color brand = Color(0xFF2F6BFF);
  static const Color brand600 = Color(0xFF1D55E8);
  static const Color brandWeak = Color(0xFFECF2FF);
  static const Color brandWeak2 = Color(0xFFDCE8FF);

  // ---------- 背景与面 ----------
  /// 页面底色（极浅灰，衬托白卡）
  static const Color bg = Color(0xFFF5F6FA);

  /// 卡片 / 导航面
  static const Color surface = Color(0xFFFFFFFF);

  /// 内嵌浅底（输入框、缩略图占位、分段控件底）
  static const Color sunken = Color(0xFFF6F7FB);

  /// 卡片描边（代替重阴影，让浅色页更利落）
  static const Color cardBorder = Color(0xFFEEF0F6);

  static const Color cameraBg = Color(0xFF10131A);

  // ---------- 文本层级 ----------
  static const Color t1 = Color(0xFF1D2129);
  static const Color t2 = Color(0xFF4E5969);
  static const Color t3 = Color(0xFF86909C);
  static const Color t4 = Color(0xFFC9CDD4);

  // ---------- 分割线 ----------
  static const Color line = Color(0xFFF0F1F6);
  static const Color line2 = Color(0xFFE6E8EF);

  // ---------- 语义色 ----------
  static const Color ok = Color(0xFF00B42A);
  static const Color okWeak = Color(0xFFE8FFEA);
  static const Color warn = Color(0xFFFF7D00);
  static const Color warnWeak = Color(0xFFFFF7E8);
  static const Color danger = Color(0xFFF53F3F);
  static const Color dangerWeak = Color(0xFFFFECE8);

  // ---------- 彩色点缀（功能入口图标块，低饱和） ----------
  static const Color violet = Color(0xFF7C5CFF);
  static const Color violetWeak = Color(0xFFF1EDFF);
  static const Color cyan = Color(0xFF13A8C4);
  static const Color cyanWeak = Color(0xFFE4F6FA);
  static const Color green = Color(0xFF12B981);
  static const Color greenWeak = Color(0xFFE6F8F1);
  static const Color amber = Color(0xFFFF9A2E);
  static const Color amberWeak = Color(0xFFFFF3E3);
  static const Color pink = Color(0xFFF5527E);
  static const Color pinkWeak = Color(0xFFFFEBF1);
  static const Color indigo = Color(0xFF5B6BFF);
  static const Color indigoWeak = Color(0xFFEDEFFF);

  // ---------- 圆角 ----------
  static const double radiusSm = 8;
  static const double radiusMd = 12;
  static const double radiusLg = 16;
  static const double radiusXl = 20;

  static const BorderRadius brSm = BorderRadius.all(Radius.circular(radiusSm));
  static const BorderRadius brMd = BorderRadius.all(Radius.circular(radiusMd));
  static const BorderRadius brLg = BorderRadius.all(Radius.circular(radiusLg));
  static const BorderRadius brXl = BorderRadius.all(Radius.circular(radiusXl));
  static const BorderRadius brPill = BorderRadius.all(Radius.circular(999));

  // ---------- 间距（4pt 栅格） ----------
  static const double gap4 = 4;
  static const double gap6 = 6;
  static const double gap8 = 8;
  static const double gap10 = 10;
  static const double gap12 = 12;
  static const double gap14 = 14;
  static const double gap16 = 16;
  static const double gap18 = 18;
  static const double gap20 = 20;

  /// 页面左右内边距
  static const double pagePadding = 14;

  // ---------- 阴影（浅色系：以描边为主，阴影极轻） ----------
  static const List<BoxShadow> shadow1 = <BoxShadow>[
    BoxShadow(color: Color(0x07142B4A), blurRadius: 2, offset: Offset(0, 1)),
    BoxShadow(
      color: Color(0x0A142B4A),
      blurRadius: 10,
      offset: Offset(0, 4),
      spreadRadius: -4,
    ),
  ];

  static const List<BoxShadow> shadow2 = <BoxShadow>[
    BoxShadow(color: Color(0x0A142B4A), blurRadius: 4, offset: Offset(0, 2)),
    BoxShadow(
      color: Color(0x12142B4A),
      blurRadius: 26,
      offset: Offset(0, 12),
      spreadRadius: -12,
    ),
  ];

  /// 主按钮品牌光晕
  static const List<BoxShadow> brandGlow = <BoxShadow>[
    BoxShadow(
      color: Color(0x332F6BFF),
      blurRadius: 16,
      offset: Offset(0, 8),
      spreadRadius: -8,
    ),
  ];

  /// 底部导航 / 中央 FAB 尺寸
  static const double navBarHeight = 46;
  static const double tabBarHeight = 62;
  static const double fabSize = 56;

  /// 数字字体特性：等宽数字
  static const List<FontFeature> numFeatures = <FontFeature>[
    FontFeature.tabularFigures(),
  ];
}
