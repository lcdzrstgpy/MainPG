import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../theme/app_tokens.dart';

/// ================================================================
/// 通用组件库 —— 与 mobile-prototype/styles.css 的组件一一对应
///   AppCard / AppListCard / AppRow      → .card / .list / .row
///   AppTag / AppDot / AppBadge          → .tag / .dot / .badge
///   AppButton / AppInput / AppSegment   → .btn / .input / .segment
///   AppAvatar / AppSwitch / AppKv       → .avatar / .switch / .kv
/// ================================================================

/// 白卡：圆角 16 + 双层阴影。
class AppCard extends StatelessWidget {
  const AppCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(AppTokens.gap14),
    this.margin,
    this.onTap,
    this.radius = AppTokens.radiusLg,
    this.background,
    this.shadow = true,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final EdgeInsetsGeometry? margin;
  final VoidCallback? onTap;
  final double radius;
  final Color? background;
  final bool shadow;

  @override
  Widget build(BuildContext context) {
    final BorderRadius radiusValue = BorderRadius.circular(radius);
    return Container(
      margin: margin,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: background ?? AppTokens.surface,
          borderRadius: radiusValue,
          // 纯白卡加极浅描边，浅色页上更利落；自定义底色不加，避免违和
          border: background == null
              ? Border.all(color: AppTokens.cardBorder)
              : null,
          boxShadow: shadow ? AppTokens.shadow1 : null,
        ),
        child: Material(
          color: Colors.transparent,
          child: InkWell(
            borderRadius: radiusValue,
            onTap: onTap,
            child: Padding(padding: padding, child: child),
          ),
        ),
      ),
    );
  }
}

/// 分组列表卡：多行之间 1px 细线。
class AppListCard extends StatelessWidget {
  const AppListCard({super.key, required this.children, this.margin});

  final List<Widget> children;
  final EdgeInsetsGeometry? margin;

  @override
  Widget build(BuildContext context) {
    final List<Widget> rows = <Widget>[];
    for (int i = 0; i < children.length; i++) {
      if (i > 0) rows.add(const _ListDivider());
      rows.add(children[i]);
    }
    return Container(
      margin: margin,
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        color: AppTokens.surface,
        borderRadius: AppTokens.brLg,
        border: Border.all(color: AppTokens.cardBorder),
        boxShadow: AppTokens.shadow1,
      ),
      // 提供 Material 祖先，供行内 InkWell 使用
      child: Material(
        color: Colors.transparent,
        child: Column(children: rows),
      ),
    );
  }
}

class _ListDivider extends StatelessWidget {
  const _ListDivider();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.only(left: AppTokens.gap14),
      child: Divider(height: 1, thickness: 1, color: AppTokens.line),
    );
  }
}

/// 彩色图标块（低饱和底 + 同色图标）——国内工具类 App 常见点缀。
///
/// 用于功能入口、列表行、空状态等处，颜色取自 [AppTokens] 的彩色点缀色板。
class AppIconBadge extends StatelessWidget {
  const AppIconBadge({
    super.key,
    required this.icon,
    this.color = AppTokens.brand,
    this.background,
    this.size = 36,
    this.iconSize = 19,
    this.radius = 11,
  });

  final IconData icon;
  final Color color;

  /// 底色，缺省为 [color] 的 12% 透明版
  final Color? background;
  final double size;
  final double iconSize;
  final double radius;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: background ?? color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(radius),
      ),
      child: Icon(icon, size: iconSize, color: color),
    );
  }
}

/// 分组行（白卡内的一行）。
class AppRow extends StatelessWidget {
  const AppRow({
    super.key,
    this.icon,
    this.iconColor,
    this.iconBackground,
    this.title,
    this.subtitle,
    this.trailing,
    this.onTap,
    this.danger = false,
    this.selected = false,
    this.centered = false,
    this.showChevron = false,
  });

  final IconData? icon;
  final Color? iconColor;
  final Color? iconBackground;
  final String? title;
  final String? subtitle;
  final Widget? trailing;
  final VoidCallback? onTap;
  final bool danger;
  final bool selected;
  final bool centered;
  final bool showChevron;

  @override
  Widget build(BuildContext context) {
    final Widget content;

    if (centered) {
      content = Center(
        child: Text(
          title ?? '',
          style: TextStyle(
            fontSize: 14.5,
            color: danger ? AppTokens.danger : AppTokens.t1,
          ),
        ),
      );
    } else {
      content = Row(
        children: <Widget>[
          if (icon != null) ...<Widget>[
            AppIconBadge(
              icon: icon!,
              // 未显式指定则用中性灰，避免整屏蓝色；需要点缀的行单独传色
              color: iconColor ?? AppTokens.t2,
              background: iconBackground,
            ),
            const SizedBox(width: AppTokens.gap12),
          ],
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Text(
                  title ?? '',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 14.5,
                    color: danger
                        ? AppTokens.danger
                        : (selected ? AppTokens.brand : AppTokens.t1),
                    fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                  ),
                ),
                if (subtitle != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text(
                      subtitle!,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 12, color: AppTokens.t3),
                    ),
                  ),
              ],
            ),
          ),
          if (trailing != null)
            trailing!
          else if (showChevron)
            const Icon(Icons.chevron_right, size: 16, color: AppTokens.t4),
        ],
      );
    }

    final Widget padded = Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppTokens.gap14,
        vertical: 13,
      ),
      child: content,
    );

    if (onTap == null) return padded;
    return InkWell(onTap: onTap, child: padded);
  }
}

/// 分组标题（.group-title / .section-head）
class AppSectionTitle extends StatelessWidget {
  const AppSectionTitle({
    super.key,
    required this.text,
    this.trailing,
    this.margin = const EdgeInsets.fromLTRB(
      AppTokens.gap16,
      AppTokens.gap18,
      AppTokens.gap16,
      AppTokens.gap8,
    ),
  });

  final String text;
  final Widget? trailing;
  final EdgeInsetsGeometry margin;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: margin,
      child: Row(
        children: <Widget>[
          Container(
            width: 3,
            height: 13,
            margin: const EdgeInsets.only(right: 7),
            decoration: const BoxDecoration(
              color: AppTokens.brand,
              borderRadius: AppTokens.brPill,
            ),
          ),
          Text(
            text,
            style: const TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.w600,
              color: AppTokens.t1,
            ),
          ),
          const Spacer(),
          if (trailing != null) trailing!,
        ],
      ),
    );
  }
}

/// 「更多 ›」文字按钮（.sh-more）
class AppMoreLink extends StatelessWidget {
  const AppMoreLink({super.key, required this.label, this.onTap});

  final String label;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: AppTokens.brSm,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Text(label,
                  style: const TextStyle(fontSize: 12.5, color: AppTokens.t3)),
              const Icon(Icons.chevron_right, size: 13, color: AppTokens.t3),
            ],
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------- 标签

enum AppTagTone { neutral, brand, ok, warn, danger }

class AppTag extends StatelessWidget {
  const AppTag(this.label, {super.key, this.tone = AppTagTone.neutral, this.icon});

  final String label;
  final AppTagTone tone;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final Color bg;
    final Color fg;
    switch (tone) {
      case AppTagTone.brand:
        bg = AppTokens.brandWeak;
        fg = AppTokens.brand;
        break;
      case AppTagTone.ok:
        bg = AppTokens.okWeak;
        fg = AppTokens.ok;
        break;
      case AppTagTone.warn:
        bg = AppTokens.warnWeak;
        fg = AppTokens.warn;
        break;
      case AppTagTone.danger:
        bg = AppTokens.dangerWeak;
        fg = AppTokens.danger;
        break;
      case AppTagTone.neutral:
        bg = AppTokens.line;
        fg = AppTokens.t2;
        break;
    }

    return Container(
      height: 22,
      padding: const EdgeInsets.symmetric(horizontal: 8),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          if (icon != null) ...<Widget>[
            Icon(icon, size: 13, color: fg),
            const SizedBox(width: AppTokens.gap4),
          ],
          Text(
            label,
            style: TextStyle(fontSize: 12, fontWeight: FontWeight.w500, color: fg),
          ),
        ],
      ),
    );
  }
}

class AppDot extends StatelessWidget {
  const AppDot({super.key, this.color = AppTokens.ok, this.size = 7});

  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(color: color, shape: BoxShape.circle),
    );
  }
}

class AppBadge extends StatelessWidget {
  const AppBadge({super.key, required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      constraints: const BoxConstraints(minWidth: 17),
      height: 17,
      padding: const EdgeInsets.symmetric(horizontal: 5),
      alignment: Alignment.center,
      decoration: const BoxDecoration(
        color: AppTokens.danger,
        borderRadius: AppTokens.brPill,
      ),
      child: Text(
        text,
        style: const TextStyle(
          fontSize: 11,
          fontWeight: FontWeight.w600,
          color: Colors.white,
          height: 1,
          fontFeatures: AppTokens.numFeatures,
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------- 按钮

enum AppButtonTone { primary, ghost, line, danger }

class AppButton extends StatefulWidget {
  const AppButton({
    super.key,
    required this.label,
    this.onPressed,
    this.tone = AppButtonTone.primary,
    this.block = false,
    this.small = false,
    this.icon,
    this.loading = false,
  });

  final String label;
  final VoidCallback? onPressed;
  final AppButtonTone tone;
  final bool block;
  final bool small;
  final IconData? icon;
  final bool loading;

  @override
  State<AppButton> createState() => _AppButtonState();
}

class _AppButtonState extends State<AppButton> {
  bool _pressed = false;

  void _setPressed(bool value) {
    if (!mounted || _pressed == value) return;
    setState(() => _pressed = value);
  }

  @override
  Widget build(BuildContext context) {
    final bool disabled = widget.onPressed == null || widget.loading;
    final double height = widget.small ? 33 : 46;
    final BorderRadius radius = widget.small
        ? AppTokens.brPill
        : AppTokens.brMd;

    Color bg;
    Color fg;
    BorderSide side = BorderSide.none;
    switch (widget.tone) {
      case AppButtonTone.primary:
        bg = AppTokens.brand;
        fg = Colors.white;
        break;
      case AppButtonTone.ghost:
        bg = AppTokens.brandWeak;
        fg = AppTokens.brand;
        break;
      case AppButtonTone.danger:
        bg = AppTokens.dangerWeak;
        fg = AppTokens.danger;
        break;
      case AppButtonTone.line:
        bg = AppTokens.surface;
        fg = AppTokens.t2;
        side = const BorderSide(color: AppTokens.line2);
        break;
    }

    final Widget body = Row(
      mainAxisSize: widget.block ? MainAxisSize.max : MainAxisSize.min,
      mainAxisAlignment: MainAxisAlignment.center,
      children: <Widget>[
        if (widget.loading)
          SizedBox(
            width: 16,
            height: 16,
            child: CircularProgressIndicator(strokeWidth: 2, color: fg),
          )
        else if (widget.icon != null)
          Icon(widget.icon, size: widget.small ? 15 : 18, color: fg),
        if (widget.loading || widget.icon != null)
          const SizedBox(width: AppTokens.gap6),
        Flexible(
          child: Text(
            widget.label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            softWrap: false,
            style: TextStyle(
              fontSize: widget.small ? 13 : 15,
              fontWeight: FontWeight.w600,
              color: fg,
            ),
          ),
        ),
      ],
    );

    // 按压动效：按下轻微回弹缩放（松手走 easeOutBack 弹性回弹）+ 轻触感反馈。
    final Widget button = Opacity(
      opacity: disabled ? 0.45 : 1,
      child: Listener(
        onPointerDown: disabled
            ? null
            : (_) {
                HapticFeedback.selectionClick();
                _setPressed(true);
              },
        onPointerUp: disabled ? null : (_) => _setPressed(false),
        onPointerCancel: disabled ? null : (_) => _setPressed(false),
        child: AnimatedScale(
          scale: _pressed && !disabled ? 0.955 : 1.0,
          duration: _pressed
              ? const Duration(milliseconds: 110)
              : const Duration(milliseconds: 280),
          curve: _pressed ? Curves.easeOut : Curves.easeOutBack,
          child: DecoratedBox(
            decoration: BoxDecoration(
              borderRadius: radius,
              // 主按钮用一层柔和品牌光晕代替 Material 重投影，更清爽
              boxShadow: widget.tone == AppButtonTone.primary && !disabled
                  ? AppTokens.brandGlow
                  : null,
            ),
            child: Material(
              color: bg,
              elevation: 0,
              shape: RoundedRectangleBorder(borderRadius: radius, side: side),
              clipBehavior: Clip.antiAlias,
              child: InkWell(
                onTap: disabled ? null : widget.onPressed,
                child: Container(
                  height: height,
                  padding:
                      EdgeInsets.symmetric(horizontal: widget.small ? 13 : 18),
                  alignment: Alignment.center,
                  child: body,
                ),
              ),
            ),
          ),
        ),
      ),
    );

    if (widget.block) return SizedBox(width: double.infinity, child: button);
    return button;
  }
}

/// 顶部导航条图标按钮（.nav-btn）
class AppNavIconButton extends StatelessWidget {
  const AppNavIconButton({
    super.key,
    required this.icon,
    this.onTap,
    this.color = AppTokens.t1,
    this.size = 21,
  });

  final IconData icon;
  final VoidCallback? onTap;
  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 30,
      height: 30,
      child: Material(
        color: Colors.transparent,
        shape: const CircleBorder(),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onTap,
          child: Icon(icon, size: size, color: color),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------- 表单

/// 输入框（.input）：48 高、浅灰底、聚焦变白+品牌描边。
class AppInput extends StatefulWidget {
  const AppInput({
    super.key,
    this.controller,
    this.hint,
    this.obscure = false,
    this.icon,
    this.suffix,
    this.readOnly = false,
    this.keyboardType,
    this.onSubmitted,
    this.onChanged,
  });

  final TextEditingController? controller;
  final String? hint;
  final bool obscure;
  final IconData? icon;
  final Widget? suffix;
  final bool readOnly;
  final TextInputType? keyboardType;
  final ValueChanged<String>? onSubmitted;
  final ValueChanged<String>? onChanged;

  @override
  State<AppInput> createState() => _AppInputState();
}

class _AppInputState extends State<AppInput> {
  final FocusNode _focus = FocusNode();
  bool _focused = false;

  @override
  void initState() {
    super.initState();
    _focus.addListener(() {
      if (!mounted) return;
      setState(() => _focused = _focus.hasFocus);
    });
  }

  @override
  void dispose() {
    _focus.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 48,
      padding: const EdgeInsets.symmetric(horizontal: AppTokens.gap14),
      decoration: BoxDecoration(
        // 未聚焦：浅灰内嵌底 + 极浅描边；聚焦：白底 + 品牌描边
        color: _focused ? AppTokens.surface : AppTokens.sunken,
        borderRadius: AppTokens.brMd,
        border: Border.all(
          color: _focused ? AppTokens.brand : AppTokens.cardBorder,
          width: _focused ? 1.5 : 1,
        ),
      ),
      child: Row(
        children: <Widget>[
          if (widget.icon != null) ...<Widget>[
            Icon(widget.icon, size: 18, color: AppTokens.t3),
            const SizedBox(width: 9),
          ],
          Expanded(
            child: TextField(
              controller: widget.controller,
              focusNode: _focus,
              obscureText: widget.obscure,
              readOnly: widget.readOnly,
              keyboardType: widget.keyboardType,
              onSubmitted: widget.onSubmitted,
              onChanged: widget.onChanged,
              style: const TextStyle(fontSize: 14.5, color: AppTokens.t1),
              decoration: InputDecoration(
                isDense: true,
                border: InputBorder.none,
                hintText: widget.hint,
                hintStyle: const TextStyle(fontSize: 14.5, color: AppTokens.t3),
              ),
            ),
          ),
          if (widget.suffix != null) widget.suffix!,
        ],
      ),
    );
  }
}

/// 表单字段（标签 + 控件）
class AppField extends StatelessWidget {
  const AppField({super.key, required this.label, required this.child});

  final String label;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: AppTokens.gap14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            label,
            style: const TextStyle(
              fontSize: 13,
              fontWeight: FontWeight.w500,
              color: AppTokens.t2,
            ),
          ),
          const SizedBox(height: 7),
          child,
        ],
      ),
    );
  }
}

/// 浅灰说明块（.env-note）
class AppEnvNote extends StatelessWidget {
  const AppEnvNote({
    super.key,
    required this.child,
    this.margin = const EdgeInsets.fromLTRB(
      AppTokens.pagePadding,
      AppTokens.gap12,
      AppTokens.pagePadding,
      0,
    ),
  });

  final Widget child;
  final EdgeInsetsGeometry margin;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: margin,
      padding: const EdgeInsets.symmetric(horizontal: AppTokens.gap12, vertical: 10),
      decoration: const BoxDecoration(
        color: Color(0xFFF2F4F8),
        borderRadius: AppTokens.brMd,
      ),
      child: DefaultTextStyle(
        style: const TextStyle(fontSize: 11.5, color: AppTokens.t3, height: 1.6),
        child: child,
      ),
    );
  }
}

/// 灰色小字提示（.hint）
class AppHint extends StatelessWidget {
  const AppHint({
    super.key,
    required this.text,
    this.alignCenter = false,
    this.margin = const EdgeInsets.fromLTRB(2, AppTokens.gap10, 2, 0),
  });

  final String text;
  final bool alignCenter;
  final EdgeInsetsGeometry margin;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: margin,
      child: Text(
        text,
        textAlign: alignCenter ? TextAlign.center : TextAlign.start,
        style: const TextStyle(fontSize: 12, color: AppTokens.t3, height: 1.6),
      ),
    );
  }
}

/// 空状态（.empty）：彩色圆角图标块 + 标题 +（可选）描述。
class AppEmptyState extends StatelessWidget {
  const AppEmptyState({
    super.key,
    required this.text,
    this.icon = Icons.inbox_outlined,
    this.description,
    this.color = AppTokens.brand,
  });

  final String text;
  final IconData icon;
  final String? description;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 40),
      child: Column(
        children: <Widget>[
          AppIconBadge(
            icon: icon,
            color: color,
            size: 62,
            iconSize: 29,
            radius: 20,
          ),
          const SizedBox(height: 14),
          Text(
            text,
            textAlign: TextAlign.center,
            style: const TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.w600,
              color: AppTokens.t2,
            ),
          ),
          if (description != null) ...<Widget>[
            const SizedBox(height: 6),
            Text(
              description!,
              textAlign: TextAlign.center,
              style: const TextStyle(
                fontSize: 12.5,
                color: AppTokens.t3,
                height: 1.6,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// 开关（.switch）
class AppSwitch extends StatelessWidget {
  const AppSwitch({super.key, required this.value, this.onChanged});

  final bool value;
  final ValueChanged<bool>? onChanged;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onChanged == null ? null : () => onChanged!(!value),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 220),
        curve: Curves.easeOutCubic,
        width: 44,
        height: 26,
        decoration: BoxDecoration(
          color: value ? AppTokens.brand : const Color(0xFFD8DBE2),
          borderRadius: AppTokens.brPill,
        ),
        child: AnimatedAlign(
          duration: const Duration(milliseconds: 220),
          curve: Curves.easeOutCubic,
          alignment: value ? Alignment.centerRight : Alignment.centerLeft,
          child: Padding(
            padding: const EdgeInsets.all(3),
            child: Container(
              width: 20,
              height: 20,
              decoration: const BoxDecoration(
                color: Colors.white,
                shape: BoxShape.circle,
                boxShadow: <BoxShadow>[
                  BoxShadow(
                    color: Color(0x40102A4A),
                    blurRadius: 3,
                    offset: Offset(0, 1),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// 分段控件（.segment）
class AppSegment extends StatelessWidget {
  const AppSegment({
    super.key,
    required this.items,
    required this.index,
    required this.onChanged,
    this.margin = const EdgeInsets.fromLTRB(
      AppTokens.pagePadding,
      AppTokens.gap4,
      AppTokens.pagePadding,
      AppTokens.gap4,
    ),
  });

  final List<String> items;
  final int index;
  final ValueChanged<int> onChanged;
  final EdgeInsetsGeometry margin;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: margin,
      padding: const EdgeInsets.all(3),
      decoration: const BoxDecoration(
        color: Color(0xFFECEEF4),
        borderRadius: BorderRadius.all(Radius.circular(11)),
      ),
      child: Row(
        children: <Widget>[
          for (int i = 0; i < items.length; i++)
            Expanded(
              child: Material(
                color: Colors.transparent,
                child: InkWell(
                  borderRadius: AppTokens.brSm,
                  onTap: () => onChanged(i),
                  child: Container(
                    height: 33,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: i == index ? AppTokens.surface : Colors.transparent,
                      borderRadius: AppTokens.brSm,
                      boxShadow: i == index ? AppTokens.shadow1 : null,
                    ),
                    child: Text(
                      items[i],
                      style: TextStyle(
                        fontSize: 13.5,
                        fontWeight:
                            i == index ? FontWeight.w600 : FontWeight.w500,
                        color: i == index ? AppTokens.t1 : AppTokens.t2,
                      ),
                    ),
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// 头像（.avatar）
class AppAvatar extends StatelessWidget {
  const AppAvatar({
    super.key,
    required this.text,
    this.size = 52,
    this.radius = 16,
    this.fontSize = 21,
    this.gradient = true,
  });

  final String text;
  final double size;
  final double radius;
  final double fontSize;
  final bool gradient;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        gradient: gradient
            ? const LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: <Color>[Color(0xFF6F9BFF), AppTokens.brand],
              )
            : null,
        color: gradient ? null : const Color(0xFFEEF1F6),
        borderRadius: BorderRadius.circular(radius),
      ),
      child: Text(
        text,
        style: TextStyle(
          fontSize: fontSize,
          fontWeight: FontWeight.w600,
          color: gradient ? Colors.white : AppTokens.t2,
        ),
      ),
    );
  }
}

/// 键值行（.kv）
class AppKv extends StatelessWidget {
  const AppKv({super.key, required this.k, required this.v, this.valueStyle});

  final String k;
  final String v;
  final TextStyle? valueStyle;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 11),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(k, style: const TextStyle(fontSize: 13.5, color: AppTokens.t3)),
          const SizedBox(width: AppTokens.gap14),
          Expanded(
            child: Text(
              v,
              textAlign: TextAlign.right,
              style: valueStyle ??
                  const TextStyle(fontSize: 13.5, color: AppTokens.t1),
            ),
          ),
        ],
      ),
    );
  }
}

/// 键值卡（白卡 + 行间细线）
class AppKvList extends StatelessWidget {
  const AppKvList({
    super.key,
    required this.items,
    this.margin = const EdgeInsets.fromLTRB(
      AppTokens.pagePadding,
      AppTokens.gap12,
      AppTokens.pagePadding,
      0,
    ),
  });

  final List<AppKv> items;
  final EdgeInsetsGeometry margin;

  @override
  Widget build(BuildContext context) {
    final List<Widget> rows = <Widget>[];
    for (int i = 0; i < items.length; i++) {
      if (i > 0) {
        rows.add(
          const Divider(height: 1, thickness: 1, color: AppTokens.line),
        );
      }
      rows.add(items[i]);
    }
    return AppCard(
      margin: margin,
      padding: const EdgeInsets.symmetric(horizontal: AppTokens.gap16),
      child: Column(children: rows),
    );
  }
}

/// 千分位格式化（10000 → 10,000），对齐原型 `.num` 展示。
String formatThousands(int value) {
  final String raw = value.abs().toString();
  final StringBuffer buffer = StringBuffer(value < 0 ? '-' : '');
  for (int i = 0; i < raw.length; i++) {
    if (i > 0 && (raw.length - i) % 3 == 0) buffer.write(',');
    buffer.write(raw[i]);
  }
  return buffer.toString();
}
