import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../core/session/session_manager.dart';
import '../../features/camera/pages/camera_page.dart';
import '../../features/capture/pages/capture_page.dart';
import '../../features/capture/state/capture_store.dart';
import '../../features/device/state/device_store.dart';
import '../../features/home/pages/home_page.dart';
import '../../features/inbox/pages/inbox_page.dart';
import '../../features/inbox/state/inbox_store.dart';
import '../../features/mine/pages/mine_page.dart';
import '../../features/points/state/wallet_store.dart';
import '../../shared/theme/app_tokens.dart';
import '../../shared/widgets/app_blocks.dart';

/// 底部导航编排：首页 / 采集 / 拍照(中央凸起) / 消息(红点) / 我的。
///
/// 拍照页为深色页面，因此 Scaffold 底色随 Tab 切换（同时驱动 dock 缺口与 FAB 光圈颜色）。
class AppShell extends StatefulWidget {
  const AppShell({super.key});

  /// 拍照 Tab 索引（由中央 FAB 唤起）
  static const int cameraIndex = 2;

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int _index = 0;
  bool _bootstrapped = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _bootstrap());
  }

  /// 登录后首次进入主壳：并行拉取钱包 / 消息 / 设备 / 采集队列。
  Future<void> _bootstrap() async {
    if (!mounted || _bootstrapped) return;
    _bootstrapped = true;
    final SessionManager session = context.read<SessionManager>();
    final WalletStore wallet = context.read<WalletStore>();
    final InboxStore inbox = context.read<InboxStore>();
    final DeviceStore device = context.read<DeviceStore>();
    final CaptureStore capture = context.read<CaptureStore>();
    inbox.setAccountId(session.accountId);
    await Future.wait<void>(<Future<void>>[
      wallet.load(),
      inbox.bootstrap(),
      device.init(),
      capture.init(),
    ]);
  }

  void _select(int index) {
    if (_index == index) return;
    setState(() => _index = index);
  }

  @override
  Widget build(BuildContext context) {
    final int unread = context.watch<InboxStore>().unreadCount;
    final bool dark = _index == AppShell.cameraIndex;

    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: (dark ? SystemUiOverlayStyle.light : SystemUiOverlayStyle.dark)
          .copyWith(statusBarColor: Colors.transparent),
      child: Scaffold(
        backgroundColor: dark ? AppTokens.cameraBg : AppTokens.bg,
        body: IndexedStack(
          index: _index,
          children: <Widget>[
            HomePage(onNavigate: _select),
            const CapturePage(),
            const CameraPage(),
            const InboxPage(),
            MinePage(onNavigate: _select),
          ],
        ),
        floatingActionButton: _CameraFab(
          active: dark,
          onTap: () => _select(AppShell.cameraIndex),
        ),
        floatingActionButtonLocation: FloatingActionButtonLocation.centerDocked,
        bottomNavigationBar: _DockBar(
          current: _index,
          unread: unread,
          onSelect: _select,
        ),
      ),
    );
  }
}

class _DockItem {
  const _DockItem(this.index, this.label, this.icon, this.activeIcon);

  final int index;
  final String label;
  final IconData icon;
  final IconData activeIcon;
}

/// 底部栏只放 4 项，中间留给拍照 FAB。
const List<_DockItem> _dockItems = <_DockItem>[
  _DockItem(0, '首页', Icons.home_outlined, Icons.home_rounded),
  _DockItem(1, '采集', Icons.link_outlined, Icons.link),
  _DockItem(3, '消息', Icons.notifications_outlined, Icons.notifications_rounded),
  _DockItem(4, '我的', Icons.person_outline, Icons.person_rounded),
];

class _DockBar extends StatelessWidget {
  const _DockBar({
    required this.current,
    required this.unread,
    required this.onSelect,
  });

  final int current;
  final int unread;
  final ValueChanged<int> onSelect;

  @override
  Widget build(BuildContext context) {
    final double bottomInset = MediaQuery.paddingOf(context).bottom;

    return BottomAppBar(
      color: AppTokens.surface,
      surfaceTintColor: Colors.transparent,
      // 轻投影 + 缺口塑形：dock 与内容区分层，但不过分厚重
      elevation: 8,
      shadowColor: const Color(0x1A10131A),
      height: AppTokens.tabBarHeight + bottomInset,
      padding: EdgeInsets.zero,
      // 缺口留出 6px 光圈，颜色跟随 Scaffold 底色（拍照页为深色）
      notchMargin: 6,
      shape: const CircularNotchedRectangle(),
      child: Padding(
        padding: EdgeInsets.only(bottom: bottomInset),
        child: Row(
          children: <Widget>[
            _button(_dockItems[0]),
            _button(_dockItems[1]),
            const SizedBox(width: 72),
            _button(_dockItems[2]),
            _button(_dockItems[3]),
          ],
        ),
      ),
    );
  }

  Widget _button(_DockItem item) {
    final bool selected = current == item.index;
    final Color color = selected ? AppTokens.brand : AppTokens.t3;

    return Expanded(
      child: InkWell(
        onTap: () => onSelect(item.index),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: <Widget>[
            // 选中态：品牌浅底 pill 包裹图标，形成明确落点
            AnimatedContainer(
              duration: const Duration(milliseconds: 220),
              curve: Curves.easeOutCubic,
              padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 3),
              decoration: BoxDecoration(
                color: selected ? AppTokens.brandWeak : Colors.transparent,
                borderRadius: AppTokens.brPill,
              ),
              child: Stack(
                clipBehavior: Clip.none,
                children: <Widget>[
                  Icon(
                    selected ? item.activeIcon : item.icon,
                    size: 23,
                    color: color,
                  ),
                  if (item.index == 3 && unread > 0)
                    Positioned(
                      top: -4,
                      right: -12,
                      child: AppBadge(text: unread > 99 ? '99+' : '$unread'),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 3),
            Text(
              item.label,
              style: TextStyle(
                fontSize: 10.5,
                color: color,
                fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _CameraFab extends StatelessWidget {
  const _CameraFab({required this.active, required this.onTap});

  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return FloatingActionButton(
      onPressed: onTap,
      backgroundColor: active ? AppTokens.brand600 : AppTokens.brand,
      foregroundColor: Colors.white,
      elevation: 6,
      highlightElevation: 6,
      shape: const CircleBorder(),
      tooltip: '拍照上传',
      child: const Icon(Icons.photo_camera_outlined, size: 27),
    );
  }
}
