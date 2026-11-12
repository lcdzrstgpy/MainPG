import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../../core/config/app_config.dart';
import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../../../shared/widgets/app_toast.dart';
import '../../device/pages/device_page.dart';
import '../../device/state/device_store.dart';
import '../../inbox/state/inbox_store.dart';
import 'password_page.dart';

/// 设置：连接（真实通道状态 / 探活）· 安全与关于
class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key});

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  Future<void> _copyAddress(String value) async {
    if (value.isEmpty) {
      showAppToast(context, '尚未登记 PC 地址');
      return;
    }
    await Clipboard.setData(ClipboardData(text: value));
    if (!mounted) return;
    showAppToast(context, '已复制到剪贴板');
  }

  Future<void> _probe(DeviceStore device) async {
    await device.probeAll();
    if (!mounted) return;
    showAppToast(context, '已重新探活：${device.channelLabel}');
  }

  Future<void> _clearCache(InboxStore inbox) async {
    await inbox.clearCache();
    if (!mounted) return;
    showAppToast(context, '本地消息缓存已清除');
  }

  @override
  Widget build(BuildContext context) {
    final DeviceStore device = context.watch<DeviceStore>();
    final InboxStore inbox = context.read<InboxStore>();
    final String address = device.current?.baseUrl ?? '';

    return AppSubPage(
      title: '设置',
      child: ListView(
        padding: const EdgeInsets.only(top: 10, bottom: 24),
        children: <Widget>[
          const AppSectionTitle(
            text: '连接',
            margin: EdgeInsets.fromLTRB(
              AppTokens.gap16,
              0,
              AppTokens.gap16,
              AppTokens.gap8,
            ),
          ),
          AppListCard(
            margin: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
            children: <Widget>[
              AppRow(
                icon: Icons.desktop_windows_outlined,
                title: '已登记 PC',
                subtitle: '${device.deviceCount} 台',
                showChevron: true,
                onTap: () => pushPage<void>(context, const DevicePage()),
              ),
              AppRow(
                icon: device.lanAvailable ? Icons.wifi : Icons.cloud_outlined,
                title: '当前通道',
                subtitle: device.lanAvailable
                    ? '走局域网直连 PC'
                    : '走云端直连（auth-api / publish-api）',
                trailing: AppTag(
                  device.channelLabel,
                  tone: device.lanAvailable ? AppTagTone.ok : AppTagTone.brand,
                ),
                onTap: () => _probe(device),
              ),
              AppRow(
                icon: Icons.cloud_outlined,
                title: 'PC 服务地址',
                subtitle: address.isEmpty ? '尚未登记' : address,
                trailing: const Icon(
                  Icons.copy_outlined,
                  size: 16,
                  color: AppTokens.t4,
                ),
                onTap: () => _copyAddress(address),
              ),
              AppRow(
                icon: Icons.sync_outlined,
                title: '重新探活',
                subtitle: device.isProbing ? '探测中…' : '检测所有已登记 PC 是否在线',
                trailing: device.isProbing
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(
                        Icons.chevron_right,
                        size: 16,
                        color: AppTokens.t4,
                      ),
                onTap: device.isProbing ? null : () => _probe(device),
              ),
            ],
          ),
          const AppSectionTitle(text: '安全与关于'),
          AppListCard(
            margin: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
            children: <Widget>[
              AppRow(
                icon: Icons.lock_outline,
                title: '修改密码',
                showChevron: true,
                onTap: () => pushPage<void>(context, const PasswordPage()),
              ),
              AppRow(
                icon: Icons.delete_outline,
                title: '清除本地缓存',
                subtitle: '消息列表缓存（已读状态保留）',
                trailing: const Icon(
                  Icons.chevron_right,
                  size: 16,
                  color: AppTokens.t4,
                ),
                onTap: () => _clearCache(inbox),
              ),
              const AppRow(
                icon: Icons.verified_user_outlined,
                title: 'App 版本',
                trailing: Text(
                  'v${AppConfig.appVersion}',
                  style: TextStyle(
                    fontSize: 13,
                    color: AppTokens.t3,
                    fontFeatures: AppTokens.numFeatures,
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
