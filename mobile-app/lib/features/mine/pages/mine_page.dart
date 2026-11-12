import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../core/session/session_manager.dart';
import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../../../shared/widgets/app_toast.dart';
import '../../device/pages/device_page.dart';
import '../../device/state/device_store.dart';
import '../../points/pages/points_page.dart';
import '../../points/state/wallet_store.dart';
import '../../settings/pages/password_page.dart';
import '../../settings/pages/settings_page.dart';
import '../../topup/pages/topup_page.dart';

/// 我的：账号信息 · 设备绑定 / 积分 / 充值 · 安全设置 · 退出登录
class MinePage extends StatelessWidget {
  const MinePage({super.key, required this.onNavigate});

  /// 保留 Shell 回调，便于后续从「我的」跳转到其他 Tab
  final ValueChanged<int> onNavigate;

  @override
  Widget build(BuildContext context) {
    final SessionManager session = context.watch<SessionManager>();
    final DeviceStore device = context.watch<DeviceStore>();
    final WalletStore wallet = context.watch<WalletStore>();

    final String name = session.displayName;
    final String email = session.email.isEmpty ? '—' : session.email;
    final String plan = wallet.planLabel;
    final String expire = wallet.planExpireAt;

    return AppScreen(
      navBar: const AppNavBar(title: '我的', leading: SizedBox(width: 30)),
      child: ListView(
        padding: EdgeInsets.only(
          bottom: tabPageBottomInset(context) + AppTokens.gap16,
        ),
        children: <Widget>[
          _head(name, email, plan, expire),
          AppListCard(
            margin: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
            children: <Widget>[
              AppRow(
                icon: Icons.desktop_windows_outlined,
                iconColor: AppTokens.indigo,
                title: '设备绑定',
                subtitle: '${device.deviceCount} 台 PC 已登记 · ${device.channelLabel}',
                trailing: const _RowValue('管理'),
                onTap: () => pushPage<void>(context, const DevicePage()),
              ),
              AppRow(
                icon: Icons.star_outline,
                iconColor: AppTokens.amber,
                title: '积分中心',
                subtitle: '签到领积分 · 查看明细',
                trailing: _RowValue(formatThousands(wallet.points)),
                onTap: () => pushPage<void>(context, const PointsPage()),
              ),
              AppRow(
                icon: Icons.account_balance_wallet_outlined,
                iconColor: AppTokens.green,
                title: '在线充值',
                subtitle: '支付宝 · 套餐与订单',
                showChevron: true,
                onTap: () => pushPage<void>(context, const TopupPage()),
              ),
            ],
          ),
          const SizedBox(height: AppTokens.gap12),
          AppListCard(
            margin: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
            children: <Widget>[
              AppRow(
                icon: Icons.edit_outlined,
                iconColor: AppTokens.cyan,
                title: '修改用户名',
                trailing: _RowValue(name),
                onTap: () => showAppToast(context, '用户名修改需在 PC 端工作台完成'),
              ),
              AppRow(
                icon: Icons.lock_outline,
                iconColor: AppTokens.violet,
                title: '修改密码',
                showChevron: true,
                onTap: () => pushPage<void>(context, const PasswordPage()),
              ),
              AppRow(
                icon: Icons.settings_outlined,
                iconColor: AppTokens.t2,
                title: '设置',
                showChevron: true,
                onTap: () => pushPage<void>(context, const SettingsPage()),
              ),
            ],
          ),
          const SizedBox(height: AppTokens.gap12),
          AppCard(
            margin: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
            padding: EdgeInsets.zero,
            onTap: () => _logout(context),
            child: const AppRow(centered: true, danger: true, title: '退出登录'),
          ),
        ],
      ),
    );
  }

  Future<void> _logout(BuildContext context) async {
    final SessionManager session = context.read<SessionManager>();
    showAppToast(context, '已退出登录');
    await session.logout();
  }

  Widget _head(String name, String email, String plan, String expire) {
    final String avatarText =
        name.isEmpty || name == '未登录' ? 'M' : name.substring(0, 1);
    return Padding(
      padding: const EdgeInsets.fromLTRB(18, 8, 18, 18),
      child: Row(
        children: <Widget>[
          AppAvatar(text: avatarText),
          const SizedBox(width: AppTokens.gap14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w700,
                    letterSpacing: -0.1,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  email,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 12.5, color: AppTokens.t3),
                ),
                const SizedBox(height: 7),
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: <Widget>[
                    AppTag(plan, tone: AppTagTone.brand),
                    if (expire.isNotEmpty)
                      AppTag('有效期至 $expire', tone: AppTagTone.ok),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// 行尾「文本 + 箭头」
class _RowValue extends StatelessWidget {
  const _RowValue(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        Text(
          text,
          style: const TextStyle(fontSize: 13, color: AppTokens.t3),
        ),
        const SizedBox(width: 6),
        const Icon(Icons.chevron_right, size: 16, color: AppTokens.t4),
      ],
    );
  }
}
