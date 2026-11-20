import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../core/session/session_manager.dart';
import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../../../shared/widgets/app_toast.dart';
import '../state/device_store.dart';

/// 设备绑定：手动登记 PC 地址 · 真实探活 · 通道切换 · 局域网登录
///
/// PC 端没有配对码 / 设备发现接口，因此这里不做扫码，改为手动录入地址。
class DevicePage extends StatefulWidget {
  const DevicePage({super.key});

  @override
  State<DevicePage> createState() => _DevicePageState();
}

class _DevicePageState extends State<DevicePage> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      context.read<DeviceStore>().probeAll();
    });
  }

  Future<void> _add(DeviceStore store) async {
    final TextEditingController name = TextEditingController();
    final TextEditingController address = TextEditingController();
    final bool? ok = await showDialog<bool>(
      context: context,
      builder: (BuildContext dialogContext) => AlertDialog(
        backgroundColor: AppTokens.surface,
        title: const Text('登记 PC 工作台', style: TextStyle(fontSize: 16)),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            AppInput(
              controller: name,
              icon: Icons.badge_outlined,
              hint: '名称（可留空，默认用地址）',
            ),
            const SizedBox(height: AppTokens.gap10),
            AppInput(
              controller: address,
              icon: Icons.lan_outlined,
              hint: '如 192.168.1.24 或 192.168.1.24:8010',
            ),
          ],
        ),
        actionsPadding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('登记'),
          ),
        ],
      ),
    );
    final String nameText = name.text;
    final String addressText = address.text;
    name.dispose();
    address.dispose();
    if (ok != true || !mounted) return;

    final String? error =
        await store.addDevice(name: nameText, address: addressText);
    if (!mounted) return;
    if (error != null) {
      showAppToast(context, error);
      return;
    }
    showAppToast(
      context,
      store.current?.online == true ? '已登记，PC 在线' : '已登记，暂未探测到 PC',
    );
  }

  Future<void> _loginLan(DeviceStore store) async {
    final SessionManager session = context.read<SessionManager>();
    final String identifier = session.identifier;
    if (identifier.isEmpty) {
      showAppToast(context, '缺少账号信息，请重新登录后再试');
      return;
    }
    final TextEditingController password = TextEditingController();
    final bool? ok = await showDialog<bool>(
      context: context,
      builder: (BuildContext dialogContext) => AlertDialog(
        backgroundColor: AppTokens.surface,
        title: const Text('登录 PC 工作台', style: TextStyle(fontSize: 16)),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(
              '账号：$identifier',
              style: const TextStyle(fontSize: 12.5, color: AppTokens.t3),
            ),
            const SizedBox(height: AppTokens.gap10),
            AppInput(
              controller: password,
              obscure: true,
              icon: Icons.lock_outline,
              hint: 'PC 端登录密码',
            ),
            const SizedBox(height: AppTokens.gap10),
            const Text(
              'PC 端为单端登录，登录后原 PC 会话会被挤掉。',
              style: TextStyle(fontSize: 12, color: AppTokens.warn, height: 1.5),
            ),
          ],
        ),
        actionsPadding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('登录'),
          ),
        ],
      ),
    );
    final String passwordText = password.text;
    password.dispose();
    if (ok != true || !mounted) return;

    showAppToast(context, '正在连接 PC 工作台…');
    final String? error = await store.loginLan(
      identifier: identifier,
      password: passwordText,
    );
    if (!mounted) return;
    showAppToast(context, error ?? '已登录 PC 工作台 · ${store.channelLabel}');
  }

  @override
  Widget build(BuildContext context) {
    final DeviceStore store = context.watch<DeviceStore>();

    return AppSubPage(
      title: '设备绑定',
      actions: <Widget>[
        AppNavIconButton(
          icon: Icons.add,
          onTap: () => _add(store),
        ),
      ],
      child: ListView(
        padding: const EdgeInsets.only(bottom: 24),
        children: <Widget>[
          _statusCard(store),
          const AppSectionTitle(
            text: '已登记 PC',
            margin: EdgeInsets.fromLTRB(
              AppTokens.pagePadding + 2,
              AppTokens.gap18,
              AppTokens.pagePadding + 2,
              AppTokens.gap8,
            ),
          ),
          if (store.devices.isEmpty)
            const AppCard(
              margin: EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
              padding: EdgeInsets.zero,
              child: AppEmptyState(
                text: '还没有登记 PC',
                icon: Icons.desktop_windows_outlined,
              ),
            )
          else
            for (final BoundDevice device in store.devices)
              _deviceCard(store, device),
          Padding(
            padding: const EdgeInsets.fromLTRB(
              AppTokens.pagePadding,
              AppTokens.gap10,
              AppTokens.pagePadding,
              0,
            ),
            child: AppButton(
              label: '添加 PC 工作台',
              tone: AppButtonTone.line,
              block: true,
              onPressed: () => _add(store),
            ),
          ),
          const AppEnvNote(
            child: Text(
              'PC 端未提供配对码 / 设备发现接口，需在 PC 上确认其局域网地址（设置页可查）后手动登记。\n'
              '登录 PC 工作台会获取局域网令牌，用于走局域网通道；PC 端为单端登录，登录会挤掉原会话。\n'
              '采集转发与拍照直传目前无服务端接口，App 内为本地登记 / 本地成组。',
            ),
          ),
        ],
      ),
    );
  }

  Widget _statusCard(DeviceStore store) {
    final String tokenState = store.hasLanToken ? '已持有局域网令牌' : '未登录 PC 工作台';
    return AppCard(
      margin: const EdgeInsets.fromLTRB(
        AppTokens.pagePadding,
        AppTokens.gap8,
        AppTokens.pagePadding,
        0,
      ),
      padding: const EdgeInsets.all(AppTokens.gap16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Icon(
                store.lanAvailable ? Icons.wifi : Icons.cloud_outlined,
                size: 18,
                color: store.lanAvailable ? AppTokens.ok : AppTokens.brand,
              ),
              const SizedBox(width: AppTokens.gap8),
              Text(
                store.channelLabel,
                style: const TextStyle(
                  fontSize: 15,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const Spacer(),
              AppTag(
                tokenState,
                tone: store.hasLanToken ? AppTagTone.ok : AppTagTone.brand,
              ),
            ],
          ),
          const SizedBox(height: AppTokens.gap10),
          Text(
            store.current == null
                ? '尚未登记 PC 工作台，当前仅云端通道可用。'
                : '当前设备：${store.current!.name}（${store.current!.host}）· '
                    '${store.current!.online ? '在线' : '离线'}',
            style: const TextStyle(fontSize: 12.5, color: AppTokens.t3, height: 1.6),
          ),
          const SizedBox(height: AppTokens.gap12),
          Row(
            children: <Widget>[
              Expanded(
                child: AppButton(
                  label: store.hasLanToken ? '重新登录 PC' : '登录 PC 工作台',
                  small: true,
                  tone: AppButtonTone.ghost,
                  onPressed: store.current == null ? null : () => _loginLan(store),
                ),
              ),
              const SizedBox(width: AppTokens.gap10),
              Expanded(
                child: AppButton(
                  label: '重新探活',
                  small: true,
                  tone: AppButtonTone.line,
                  loading: store.isProbing,
                  onPressed: store.isProbing ? null : store.probeAll,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _deviceCard(DeviceStore store, BoundDevice device) {
    final bool current = store.isCurrent(device.id);
    final bool busy = store.isBusy(device.id);
    final String status = device.probing
        ? '探测中…'
        : (device.online ? '在线 · 局域网可达' : '离线 · 回落云端通道');

    return AppCard(
      margin: const EdgeInsets.fromLTRB(
        AppTokens.pagePadding,
        0,
        AppTokens.pagePadding,
        AppTokens.gap10,
      ),
      padding: const EdgeInsets.all(AppTokens.gap14),
      child: Row(
        children: <Widget>[
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(
              color: AppTokens.brandWeak,
              borderRadius: BorderRadius.circular(12),
            ),
            child: const Icon(
              Icons.desktop_windows_outlined,
              size: 20,
              color: AppTokens.brand,
            ),
          ),
          const SizedBox(width: AppTokens.gap12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  children: <Widget>[
                    Flexible(
                      child: Text(
                        device.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 14,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                    const SizedBox(width: 6),
                    AppDot(color: device.online ? AppTokens.ok : AppTokens.t4),
                  ],
                ),
                const SizedBox(height: 3),
                Text(
                  '${device.host} · $status',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 11.5, color: AppTokens.t3),
                ),
              ],
            ),
          ),
          const SizedBox(width: AppTokens.gap8),
          if (current)
            const AppTag('当前', tone: AppTagTone.ok)
          else
            AppButton(
              label: '设为当前',
              small: true,
              tone: AppButtonTone.ghost,
              loading: busy,
              onPressed: busy || store.isProbing
                  ? null
                  : () => _switchTo(store, device),
            ),
          const SizedBox(width: 2),
          AppNavIconButton(
            icon: Icons.link_off,
            size: 18,
            color: AppTokens.danger,
            onTap: () => _unbind(store, device),
          ),
        ],
      ),
    );
  }

  Future<void> _switchTo(DeviceStore store, BoundDevice device) async {
    final bool ok = await store.switchTo(device.id);
    if (!mounted || !ok) return;
    showAppToast(context, '已切换到 ${device.name} · ${store.channelLabel}');
  }

  Future<void> _unbind(DeviceStore store, BoundDevice device) async {
    await store.removeDevice(device.id);
    if (!mounted) return;
    showAppToast(context, '已移除 ${device.name}');
  }
}
