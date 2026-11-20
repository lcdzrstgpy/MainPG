import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../core/models/message_models.dart';
import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../../../shared/widgets/app_toast.dart';
import '../../capture/pages/collect_detail_page.dart';
import '../../capture/state/capture_store.dart';
import '../../capture/widgets/capture_status_tag.dart';
import '../../device/pages/device_page.dart';
import '../../device/state/device_store.dart';
import '../../inbox/state/inbox_store.dart';
import '../../points/pages/points_page.dart';
import '../../points/state/wallet_store.dart';
import 'announce_page.dart';

/// 首页：PC 连接状态 hero 卡 · 快捷动作 · 积分卡 · 公告横幅 · 采集动态
class HomePage extends StatelessWidget {
  const HomePage({super.key, required this.onNavigate});

  /// 由 Shell 注入的 Tab 切换回调
  final ValueChanged<int> onNavigate;

  @override
  Widget build(BuildContext context) {
    final DeviceStore device = context.watch<DeviceStore>();
    final WalletStore wallet = context.watch<WalletStore>();
    final CaptureStore capture = context.watch<CaptureStore>();
    final InboxStore inbox = context.watch<InboxStore>();
    final List<CaptureRecord> feed = capture.records.take(3).toList();
    final Announcement? latest =
        inbox.announcements.isEmpty ? null : inbox.announcements.first;

    return RefreshIndicator(
      onRefresh: () async {
        await Future.wait<void>(<Future<void>>[wallet.load(), inbox.load()]);
      },
      child: ListView(
        padding: EdgeInsets.zero,
        physics: const AlwaysScrollableScrollPhysics(),
        children: <Widget>[
          _HomeHero(
            device: device,
            capture: capture,
            wallet: wallet,
            onTapDevice: () => pushPage<void>(context, const DevicePage()),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(
              AppTokens.pagePadding,
              AppTokens.gap16,
              AppTokens.pagePadding,
              0,
            ),
            child: Row(
              children: <Widget>[
                Expanded(
                  child: _QuickItem(
                    icon: Icons.link,
                    title: '去采集',
                    subtitle: '粘贴链接',
                    color: AppTokens.brand,
                    onTap: () => onNavigate(1),
                  ),
                ),
                const SizedBox(width: AppTokens.gap10),
                Expanded(
                  child: _QuickItem(
                    icon: Icons.photo_camera_outlined,
                    title: '去拍照',
                    subtitle: '本地成组',
                    color: AppTokens.violet,
                    onTap: () => onNavigate(2),
                  ),
                ),
                const SizedBox(width: AppTokens.gap10),
                Expanded(
                  child: _QuickItem(
                    icon: Icons.star_outline,
                    title: '去签到',
                    subtitle: wallet.dailyClaimable
                        ? '＋${wallet.dailyClaimPoints} 积分'
                        : '今日已签',
                    color: AppTokens.amber,
                    onTap: () => _signIn(context),
                  ),
                ),
              ],
            ),
          ),
          AppSectionTitle(
            text: '我的积分',
            trailing: AppMoreLink(
              label: '明细',
              onTap: () => pushPage<void>(context, const PointsPage()),
            ),
          ),
          _PointsCard(wallet: wallet, onSignIn: () => _signIn(context)),
          _AnnounceBanner(
            announcement: latest,
            onTap: () => pushPage<void>(
              context,
              AnnouncePage(announcement: latest),
            ),
          ),
          AppSectionTitle(
            text: '采集动态',
            trailing: AppMoreLink(label: '全部', onTap: () => onNavigate(1)),
          ),
          if (feed.isEmpty)
            const AppCard(
              margin: EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
              padding: EdgeInsets.zero,
              child: AppEmptyState(text: '暂无采集记录', icon: Icons.image_outlined),
            )
          else
            AppListCard(
              margin: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
              children: <Widget>[
                for (final CaptureRecord record in feed)
                  _FeedTile(
                    record: record,
                    onTap: () => pushPage<void>(
                      context,
                      CollectDetailPage(recordId: record.id),
                    ),
                  ),
              ],
            ),
          SizedBox(height: tabPageBottomInset(context) + AppTokens.gap16),
        ],
      ),
    );
  }

  Future<void> _signIn(BuildContext context) async {
    final WalletStore wallet = context.read<WalletStore>();
    final ClaimOutcome outcome = await wallet.claimDaily();
    if (!context.mounted) return;
    showAppToast(context, outcome.message);
  }
}

// ---------------------------------------------------------------- hero

class _HomeHero extends StatelessWidget {
  const _HomeHero({
    required this.device,
    required this.capture,
    required this.wallet,
    required this.onTapDevice,
  });

  final DeviceStore device;
  final CaptureStore capture;
  final WalletStore wallet;
  final VoidCallback onTapDevice;

  @override
  Widget build(BuildContext context) {
    final BoundDevice? current = device.current;
    final bool online = current?.online ?? false;
    final double topInset = MediaQuery.paddingOf(context).top;
    final String subtitle = current == null
        ? '未登记 PC 工作台，点击添加'
        : '${current.host} · ${online ? '已连通' : '未连通'}';

    return Padding(
      padding: EdgeInsets.fromLTRB(
        AppTokens.pagePadding,
        topInset + AppTokens.gap12,
        AppTokens.pagePadding,
        AppTokens.gap4,
      ),
      child: AppCard(
        padding: EdgeInsets.zero,
        child: ClipRRect(
          borderRadius: AppTokens.brLg,
          child: Stack(
            children: <Widget>[
              // 右上角柔和品牌色装饰块，替代大面积饱和蓝底
              Positioned(right: -48, top: -58, child: _blob(152, AppTokens.brandWeak)),
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 15, 16, 13),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Material(
                      color: Colors.transparent,
                      child: InkWell(
                        onTap: onTapDevice,
                        borderRadius: AppTokens.brMd,
                        child: Padding(
                          padding: const EdgeInsets.symmetric(vertical: 2),
                          child: Row(
                            children: <Widget>[
                              const AppIconBadge(
                                icon: Icons.desktop_windows_outlined,
                                color: AppTokens.brand,
                                size: 38,
                                iconSize: 20,
                                radius: 12,
                              ),
                              const SizedBox(width: AppTokens.gap10),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: <Widget>[
                                    Text(
                                      current?.name ?? '未登记 PC',
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: const TextStyle(
                                        fontSize: 15,
                                        fontWeight: FontWeight.w600,
                                        color: AppTokens.t1,
                                      ),
                                    ),
                                    const SizedBox(height: 2),
                                    Text(
                                      '${device.deviceCount} 台已登记 · $subtitle',
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: const TextStyle(
                                        fontSize: 12,
                                        color: AppTokens.t3,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                              AppTag(
                                online ? 'PC 在线' : 'PC 离线',
                                tone: online ? AppTagTone.ok : AppTagTone.warn,
                                icon: online
                                    ? Icons.check_circle_outline
                                    : Icons.cloud_off_outlined,
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(height: AppTokens.gap12),
                    Material(
                      color: Colors.transparent,
                      child: InkWell(
                        onTap: onTapDevice,
                        borderRadius: AppTokens.brPill,
                        child: Container(
                          height: 26,
                          padding: const EdgeInsets.symmetric(horizontal: 10),
                          decoration: const BoxDecoration(
                            color: AppTokens.brandWeak,
                            borderRadius: AppTokens.brPill,
                          ),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: <Widget>[
                              Icon(
                                device.lanAvailable
                                    ? Icons.wifi
                                    : Icons.cloud_outlined,
                                size: 13,
                                color: AppTokens.brand,
                              ),
                              const SizedBox(width: 5),
                              Text(
                                device.channelLabel,
                                style: const TextStyle(
                                  fontSize: 12,
                                  fontWeight: FontWeight.w500,
                                  color: AppTokens.brand,
                                ),
                              ),
                              const SizedBox(width: 2),
                              const Icon(
                                Icons.chevron_right,
                                size: 13,
                                color: AppTokens.brand,
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(height: AppTokens.gap14),
                    const Divider(height: 1, thickness: 1, color: AppTokens.line),
                    const SizedBox(height: AppTokens.gap12),
                    Row(
                      children: <Widget>[
                        _metric('${capture.todayCount}', '本机登记'),
                        _metricDivider(),
                        _metric('${capture.pendingCount}', '待 PC 导入'),
                        _metricDivider(),
                        _metric('${wallet.points}', '可用积分', highlight: true),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _blob(double size, Color color) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(color: color, shape: BoxShape.circle),
    );
  }

  Widget _metric(String value, String label, {bool highlight = false}) {
    return Expanded(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            value,
            style: TextStyle(
              fontSize: 19,
              fontWeight: FontWeight.w700,
              color: highlight ? AppTokens.brand : AppTokens.t1,
              letterSpacing: -0.2,
              fontFeatures: AppTokens.numFeatures,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            label,
            style: const TextStyle(fontSize: 11.5, color: AppTokens.t3),
          ),
        ],
      ),
    );
  }

  Widget _metricDivider() {
    return Container(
      width: 1,
      height: 30,
      margin: const EdgeInsets.symmetric(horizontal: AppTokens.gap14),
      color: AppTokens.line2,
    );
  }
}

// ---------------------------------------------------------------- 快捷动作

class _QuickItem extends StatelessWidget {
  const _QuickItem({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.onTap,
    this.color = AppTokens.brand,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final VoidCallback onTap;

  /// 图标块主题色（低饱和底 + 同色图标）
  final Color color;

  @override
  Widget build(BuildContext context) {
    return AppCard(
      onTap: onTap,
      padding: const EdgeInsets.fromLTRB(8, 14, 8, 12),
      child: Column(
        children: <Widget>[
          AppIconBadge(icon: icon, color: color, size: 40, iconSize: 21, radius: 13),
          const SizedBox(height: AppTokens.gap8),
          Text(
            title,
            style: const TextStyle(
              fontSize: 13,
              fontWeight: FontWeight.w500,
              color: AppTokens.t1,
            ),
          ),
          const SizedBox(height: 1),
          Text(
            subtitle,
            style: const TextStyle(fontSize: 11, color: AppTokens.t3),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------- 积分卡

class _PointsCard extends StatelessWidget {
  const _PointsCard({required this.wallet, required this.onSignIn});

  final WalletStore wallet;
  final VoidCallback onSignIn;

  @override
  Widget build(BuildContext context) {
    final bool claimable = wallet.dailyClaimable;
    final String state = claimable
        ? '本周已签到 ${wallet.dailyWeekCount}/${wallet.weeklyMax} 天 · 可签到'
        : '本周已签到 ${wallet.dailyWeekCount}/${wallet.weeklyMax} 天 · 今日已领';

    return AppCard(
      margin: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
      padding: const EdgeInsets.fromLTRB(16, 15, 16, 15),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  crossAxisAlignment: CrossAxisAlignment.baseline,
                  textBaseline: TextBaseline.alphabetic,
                  children: <Widget>[
                    Text(
                      formatThousands(wallet.points),
                      style: const TextStyle(
                        fontSize: 25,
                        fontWeight: FontWeight.w700,
                        letterSpacing: -0.5,
                        color: AppTokens.t1,
                        fontFeatures: AppTokens.numFeatures,
                      ),
                    ),
                    const SizedBox(width: 3),
                    const Text(
                      '积分',
                      style: TextStyle(fontSize: 13, color: AppTokens.t3),
                    ),
                  ],
                ),
                const SizedBox(height: 1),
                Text(
                  state,
                  style: const TextStyle(fontSize: 12, color: AppTokens.t3),
                ),
              ],
            ),
          ),
          AppButton(
            label: claimable ? '签到' : '已签到',
            small: true,
            onPressed: claimable ? onSignIn : null,
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------- 公告横幅

class _AnnounceBanner extends StatelessWidget {
  const _AnnounceBanner({required this.announcement, required this.onTap});

  final Announcement? announcement;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final Announcement? item = announcement;
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppTokens.pagePadding,
        AppTokens.gap12,
        AppTokens.pagePadding,
        0,
      ),
      child: Material(
        color: const Color(0xFFFFF5E6),
        borderRadius: AppTokens.brLg,
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onTap,
          child: Container(
            padding: const EdgeInsets.symmetric(
              horizontal: AppTokens.gap14,
              vertical: AppTokens.gap12,
            ),
            decoration: BoxDecoration(
              borderRadius: AppTokens.brLg,
              border: Border.all(color: const Color(0xFFFFE2B8)),
            ),
            child: Row(
              children: <Widget>[
                const Icon(Icons.campaign_outlined, size: 19, color: AppTokens.warn),
                const SizedBox(width: AppTokens.gap10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        item?.title ?? '暂无公告',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 13.5,
                          fontWeight: FontWeight.w600,
                          color: Color(0xFF8A4B00),
                        ),
                      ),
                      const SizedBox(height: 1),
                      Text(
                        item == null
                            ? '云端暂无公告，下拉可刷新'
                            : (item.content.isEmpty ? '点击查看详情' : item.content),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 12, color: Color(0xFFA9691F)),
                      ),
                    ],
                  ),
                ),
                const Icon(Icons.chevron_right, size: 16, color: Color(0xFFC98A3C)),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------- 采集动态条目

class _FeedTile extends StatelessWidget {
  const _FeedTile({required this.record, required this.onTap});

  final CaptureRecord record;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AppTokens.gap14,
            vertical: 13,
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              const AppIconBadge(
                icon: Icons.image_outlined,
                color: AppTokens.cyan,
                size: 34,
                iconSize: 18,
                radius: 10,
              ),
              const SizedBox(width: 11),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      record.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 13.5, color: AppTokens.t1),
                    ),
                    const SizedBox(height: 5),
                    Row(
                      children: <Widget>[
                        CaptureStatusTag(status: record.status),
                        const SizedBox(width: 8),
                        Text(
                          '${record.site} · ${record.time}',
                          style: const TextStyle(fontSize: 11.5, color: AppTokens.t3),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
