import 'package:flutter/material.dart';

import '../theme/app_tokens.dart';
import 'app_blocks.dart';

/// 页面骨架：统一背景色 + 可选顶部导航条 + 内容区。
///
/// 一级 Tab 页直接用 [AppScreen]（外层已有 Scaffold）；
/// 二级页用 [AppSubPage]（自带 Scaffold 与返回导航条）。
class AppScreen extends StatelessWidget {
  const AppScreen({
    super.key,
    required this.child,
    this.navBar,
    this.dark = false,
    this.background,
  });

  final Widget child;
  final Widget? navBar;
  final bool dark;
  final Color? background;

  @override
  Widget build(BuildContext context) {
    return ColoredBox(
      color: background ?? (dark ? AppTokens.cameraBg : AppTokens.bg),
      child: Column(
        children: <Widget>[
          if (navBar != null) navBar!,
          Expanded(child: child),
        ],
      ),
    );
  }
}

/// 顶部导航条（.navbar）：状态栏安全区 + 46px 内容行。
class AppNavBar extends StatelessWidget {
  const AppNavBar({
    super.key,
    this.title,
    this.onBack,
    this.leading,
    this.actions,
    this.dark = false,
  });

  final String? title;
  final VoidCallback? onBack;
  final Widget? leading;
  final List<Widget>? actions;
  final bool dark;

  @override
  Widget build(BuildContext context) {
    final double topInset = MediaQuery.paddingOf(context).top;
    final Color fg = dark ? Colors.white : AppTokens.t1;

    return Container(
      height: topInset + AppTokens.navBarHeight,
      padding: EdgeInsets.only(top: topInset),
      color: dark ? AppTokens.cameraBg : AppTokens.bg,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
        child: Row(
          children: <Widget>[
            leading ??
                (onBack != null
                    ? AppNavIconButton(
                        icon: Icons.chevron_left,
                        color: fg,
                        onTap: onBack,
                      )
                    : const SizedBox(width: 30)),
            Expanded(
              child: title == null
                  ? const SizedBox.shrink()
                  : Text(
                      title!,
                      textAlign: TextAlign.center,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w600,
                        color: fg,
                        letterSpacing: -0.1,
                      ),
                    ),
            ),
            ...(actions ?? const <Widget>[SizedBox(width: 30)]),
          ],
        ),
      ),
    );
  }
}

/// 二级页：自带 Scaffold + 返回导航条。
class AppSubPage extends StatelessWidget {
  const AppSubPage({
    super.key,
    required this.title,
    required this.child,
    this.dark = false,
    this.background,
    this.actions,
    this.bottomBar,
  });

  final String title;
  final Widget child;
  final bool dark;
  final Color? background;
  final List<Widget>? actions;
  final Widget? bottomBar;

  @override
  Widget build(BuildContext context) {
    final Color bg = background ?? (dark ? AppTokens.cameraBg : AppTokens.bg);
    return Scaffold(
      backgroundColor: bg,
      resizeToAvoidBottomInset: true,
      body: AppScreen(
        dark: dark,
        background: bg,
        navBar: AppNavBar(
          title: title,
          dark: dark,
          onBack: () => Navigator.of(context).maybePop(),
          actions: actions,
        ),
        child: child,
      ),
      bottomNavigationBar: bottomBar,
    );
  }
}

/// 打开二级页
Future<T?> pushPage<T>(BuildContext context, Widget page) {
  return Navigator.of(context).push<T>(
    MaterialPageRoute<T>(builder: (BuildContext _) => page),
  );
}

/// 一级 Tab 页底部需要预留的高度（避开底部 dock）。
double tabPageBottomInset(BuildContext context) {
  return AppTokens.tabBarHeight + 26 + MediaQuery.paddingOf(context).bottom;
}
