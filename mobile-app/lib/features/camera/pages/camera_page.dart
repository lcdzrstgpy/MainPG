import 'dart:ui' show PathMetric;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';

import '../../../core/api/asset_api.dart';
import '../../../core/error/error_mapper.dart';
import '../../../core/http/api_client.dart';
import '../../../core/session/session_manager.dart';
import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../../../shared/widgets/app_toast.dart';
import '../../device/pages/device_page.dart';

/// 拍照页（深色）：真实调用系统相机 / 相册，压缩后上传到云端账号，
/// PC 端用同一账号登录后即可在「个人资产」里看到并取用。
class CameraPage extends StatefulWidget {
  const CameraPage({super.key});

  @override
  State<CameraPage> createState() => _CameraPageState();
}

class _CameraPageState extends State<CameraPage> {
  static const int _maxUploadBytes = 8 * 1024 * 1024;

  final ImagePicker _picker = ImagePicker();

  bool _busy = false;
  bool _loadingList = false;
  List<MobileAsset> _recent = const <MobileAsset>[];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _loadRecent());
  }

  Future<void> _loadRecent() async {
    final SessionManager session = context.read<SessionManager>();
    if (!session.isLoggedIn) return;
    setState(() => _loadingList = true);
    try {
      final List<MobileAsset> items = await session.asset.list(limit: 12);
      if (!mounted) return;
      setState(() => _recent = items);
    } catch (_) {
      // 列表拉取失败不打断拍摄主流程，静默降级为「暂无记录」。
    } finally {
      if (mounted) setState(() => _loadingList = false);
    }
  }

  Future<void> _capture(ImageSource source) async {
    if (_busy) return;
    HapticFeedback.selectionClick();
    final SessionManager session = context.read<SessionManager>();
    if (!session.isLoggedIn) {
      showAppToast(context, '请先登录后再上传');
      return;
    }
    XFile? file;
    try {
      file = await _picker.pickImage(
        source: source,
        maxWidth: 1920,
        maxHeight: 1920,
        imageQuality: 85,
      );
    } catch (_) {
      if (!mounted) return;
      showAppToast(context, source == ImageSource.camera ? '无法打开相机，请检查权限' : '无法打开相册，请检查权限');
      return;
    }
    if (file == null) return; // 用户取消
    if (!mounted) return;
    await _upload(session, file, source);
  }

  Future<void> _upload(
    SessionManager session,
    XFile file,
    ImageSource source,
  ) async {
    setState(() => _busy = true);
    try {
      final Uint8List bytes = await file.readAsBytes();
      if (!mounted) return;
      if (bytes.isEmpty) {
        showAppToast(context, '读取到的图片为空，请重试');
        return;
      }
      if (bytes.length > _maxUploadBytes) {
        showAppToast(context, '图片超过 8MB，请换一张较小的图片');
        return;
      }
      final String mime = _resolveMime(file);
      final String name = _resolveFileName(file, mime);
      await session.asset.upload(
        bytes: bytes,
        fileName: name,
        mime: mime,
        assetType: source == ImageSource.camera ? 'photo' : 'gallery',
      );
      if (!mounted) return;
      HapticFeedback.mediumImpact();
      showAppToast(context, '已上传到云端，PC 端「个人资产」可查看');
      await _loadRecent();
    } on ApiException catch (error) {
      if (!mounted) return;
      HapticFeedback.heavyImpact();
      if (ErrorMapper.isSessionInvalid(error)) {
        await context.read<SessionManager>().handleSessionInvalid();
        if (!mounted) return;
      }
      showAppToast(context, ErrorMapper.toUserMessage(error));
    } catch (_) {
      if (!mounted) return;
      HapticFeedback.heavyImpact();
      showAppToast(context, '上传失败，请检查网络后重试');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _resolveMime(XFile file) {
    final String declared = (file.mimeType ?? '').toLowerCase();
    if (declared.startsWith('image/')) return declared;
    final String name = file.name.toLowerCase();
    if (name.endsWith('.png')) return 'image/png';
    if (name.endsWith('.gif')) return 'image/gif';
    if (name.endsWith('.webp')) return 'image/webp';
    return 'image/jpeg';
  }

  String _resolveFileName(XFile file, String mime) {
    final String name = file.name.trim();
    if (name.isNotEmpty) return name;
    final String ext = switch (mime) {
      'image/png' => 'png',
      'image/gif' => 'gif',
      'image/webp' => 'webp',
      _ => 'jpg',
    };
    return 'photo_${DateTime.now().millisecondsSinceEpoch}.$ext';
  }

  @override
  Widget build(BuildContext context) {
    return AppScreen(
      dark: true,
      navBar: AppNavBar(
        title: '拍照',
        dark: true,
        leading: const SizedBox(width: 30),
        actions: <Widget>[
          AppNavIconButton(
            icon: Icons.desktop_windows_outlined,
            color: Colors.white,
            onTap: () => pushPage<void>(context, const DevicePage()),
          ),
        ],
      ),
      child: ListView(
        padding: EdgeInsets.only(
          bottom: tabPageBottomInset(context) + AppTokens.gap16,
        ),
        children: <Widget>[
          _CamStage(busy: _busy),
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
                  child: _CamActionButton(
                    icon: Icons.image_outlined,
                    label: '相册',
                    busy: _busy,
                    onTap: () => _capture(ImageSource.gallery),
                  ),
                ),
                const SizedBox(width: AppTokens.gap12),
                Expanded(
                  child: _CamActionButton(
                    icon: Icons.photo_camera_outlined,
                    label: '拍照上传',
                    main: true,
                    busy: _busy,
                    onTap: () => _capture(ImageSource.camera),
                  ),
                ),
              ],
            ),
          ),
          const _CamNote(),
          _RecentAssets(
            assets: _recent,
            loading: _loadingList,
            onRefresh: _busy ? null : _loadRecent,
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------- 取景框

class _CamStage extends StatelessWidget {
  const _CamStage({required this.busy});

  final bool busy;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
      height: 268,
      decoration: const BoxDecoration(
        gradient: RadialGradient(
          center: Alignment(0, -0.84),
          radius: 1.2,
          colors: <Color>[Color(0xFF29323F), Color(0xFF141922)],
          stops: <double>[0.0, 0.7],
        ),
        borderRadius: AppTokens.brXl,
      ),
      clipBehavior: Clip.antiAlias,
      child: Stack(
        children: <Widget>[
          const Positioned.fill(child: CustomPaint(painter: _GridPainter())),
          Center(
            child: CustomPaint(
              painter: const _DashedBorderPainter(),
              child: SizedBox(
                width: 172,
                height: 172,
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: <Widget>[
                    if (busy)
                      const SizedBox(
                        width: 30,
                        height: 30,
                        child: CircularProgressIndicator(
                          strokeWidth: 2.4,
                          valueColor: AlwaysStoppedAnimation<Color>(Colors.white),
                        ),
                      )
                    else
                      const Icon(
                        Icons.photo_camera_outlined,
                        size: 34,
                        color: Color(0x9EFFFFFF),
                      ),
                    const SizedBox(height: 10),
                    Text(
                      busy ? '正在上传…' : '将商品放入取景框',
                      style: const TextStyle(fontSize: 12.5, color: Color(0x9EFFFFFF)),
                    ),
                  ],
                ),
              ),
            ),
          ),
          const Positioned(
            left: 0,
            right: 0,
            bottom: 12,
            child: Text(
              '拍照 / 相册上传 · 云端账号直连',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 11.5, color: Color(0x80FFFFFF)),
            ),
          ),
        ],
      ),
    );
  }
}

class _GridPainter extends CustomPainter {
  const _GridPainter();

  @override
  void paint(Canvas canvas, Size size) {
    final Paint paint = Paint()
      ..color = const Color(0x0BFFFFFF)
      ..strokeWidth = 1;
    for (double x = 0; x <= size.width; x += 56) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), paint);
    }
    for (double y = 0; y <= size.height; y += 56) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), paint);
    }
  }

  @override
  bool shouldRepaint(covariant _GridPainter oldDelegate) => false;
}

class _DashedBorderPainter extends CustomPainter {
  const _DashedBorderPainter();

  @override
  void paint(Canvas canvas, Size size) {
    final Paint paint = Paint()
      ..color = const Color(0x52FFFFFF)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.5
      ..strokeCap = StrokeCap.round;
    final Path path = Path()
      ..addRRect(
        RRect.fromRectAndRadius(Offset.zero & size, const Radius.circular(22)),
      );
    const double dash = 6;
    const double gap = 5;
    for (final PathMetric metric in path.computeMetrics()) {
      double distance = 0;
      while (distance < metric.length) {
        final double end = distance + dash;
        canvas.drawPath(
          metric.extractPath(distance, end > metric.length ? metric.length : end),
          paint,
        );
        distance = end + gap;
      }
    }
  }

  @override
  bool shouldRepaint(covariant _DashedBorderPainter oldDelegate) => false;
}

// ---------------------------------------------------------------- 动作按钮

class _CamActionButton extends StatefulWidget {
  const _CamActionButton({
    required this.icon,
    required this.label,
    required this.onTap,
    this.main = false,
    this.busy = false,
  });

  final IconData icon;
  final String label;
  final VoidCallback onTap;
  final bool main;
  final bool busy;

  @override
  State<_CamActionButton> createState() => _CamActionButtonState();
}

class _CamActionButtonState extends State<_CamActionButton> {
  bool _pressed = false;

  void _setPressed(bool value) {
    if (widget.busy || _pressed == value) return;
    setState(() => _pressed = value);
  }

  @override
  Widget build(BuildContext context) {
    final bool disabled = widget.busy;
    return GestureDetector(
      onTapDown: (_) => _setPressed(true),
      onTapUp: (_) => _setPressed(false),
      onTapCancel: () => _setPressed(false),
      onTap: disabled ? null : widget.onTap,
      child: AnimatedScale(
        scale: _pressed ? 0.95 : 1.0,
        duration: const Duration(milliseconds: 120),
        curve: Curves.easeOutCubic,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 140),
          curve: Curves.easeOut,
          height: 52,
          decoration: BoxDecoration(
            color: widget.main ? AppTokens.brand : const Color(0x12FFFFFF),
            borderRadius: AppTokens.brLg,
            border: widget.main
                ? null
                : Border.all(color: const Color(0x24FFFFFF)),
            boxShadow: widget.main
                ? const <BoxShadow>[
                    BoxShadow(
                      color: Color(0x662F6BFF),
                      blurRadius: 16,
                      offset: Offset(0, 8),
                      spreadRadius: -6,
                    ),
                  ]
                : null,
          ),
          child: Opacity(
            opacity: disabled ? 0.7 : 1,
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: <Widget>[
                Icon(widget.icon, size: 20, color: Colors.white),
                const SizedBox(width: 9),
                Text(
                  widget.label,
                  style: const TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w500,
                    color: Colors.white,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _CamNote extends StatelessWidget {
  const _CamNote();

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.fromLTRB(
        AppTokens.pagePadding,
        AppTokens.gap16,
        AppTokens.pagePadding,
        0,
      ),
      padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 11),
      decoration: BoxDecoration(
        color: const Color(0x1F2F6BFF),
        borderRadius: AppTokens.brMd,
        border: Border.all(color: const Color(0x382F6BFF)),
      ),
      child: const Text(
        '拍照 / 相册会真实调用系统相机并压缩到 1920px 内，上传至云端账号（单张 ≤ 8MB）。'
        '在 PC 端用同一账号登录，即可在侧边栏「个人资产」中预览、下载或送入业务。',
        style: TextStyle(fontSize: 11.5, height: 1.6, color: Color(0xFFBBD3FF)),
      ),
    );
  }
}

// ---------------------------------------------------------------- 最近上传

class _RecentAssets extends StatelessWidget {
  const _RecentAssets({
    required this.assets,
    required this.loading,
    required this.onRefresh,
  });

  final List<MobileAsset> assets;
  final bool loading;
  final Future<void> Function()? onRefresh;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppTokens.pagePadding,
        AppTokens.gap18,
        AppTokens.pagePadding,
        0,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              const Expanded(
                child: Text(
                  '最近上传',
                  style: TextStyle(
                    fontSize: 13.5,
                    fontWeight: FontWeight.w600,
                    color: Colors.white,
                  ),
                ),
              ),
              if (onRefresh != null)
                GestureDetector(
                  onTap: () {
                    HapticFeedback.selectionClick();
                    onRefresh!.call();
                  },
                  child: const Padding(
                    padding: EdgeInsets.symmetric(horizontal: 4, vertical: 2),
                    child: Text(
                      '刷新',
                      style: TextStyle(fontSize: 12, color: Color(0x9EFFFFFF)),
                    ),
                  ),
                ),
            ],
          ),
          const SizedBox(height: AppTokens.gap10),
          if (loading)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 14),
              child: Center(
                child: SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    valueColor: AlwaysStoppedAnimation<Color>(Color(0x80FFFFFF)),
                  ),
                ),
              ),
            )
          else if (assets.isEmpty)
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(vertical: 18),
              decoration: BoxDecoration(
                color: const Color(0x0DFFFFFF),
                borderRadius: AppTokens.brMd,
                border: Border.all(color: const Color(0x1AFFFFFF)),
              ),
              child: const Text(
                '暂无上传记录',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 12, color: Color(0x80FFFFFF)),
              ),
            )
          else
            ...assets.map((MobileAsset asset) => _AssetRow(asset: asset)),
        ],
      ),
    );
  }
}

class _AssetRow extends StatelessWidget {
  const _AssetRow({required this.asset});

  final MobileAsset asset;

  @override
  Widget build(BuildContext context) {
    final bool isPhoto = asset.assetType == 'photo';
    return Container(
      margin: const EdgeInsets.only(bottom: AppTokens.gap8),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: const Color(0x0DFFFFFF),
        borderRadius: AppTokens.brMd,
        border: Border.all(color: const Color(0x1AFFFFFF)),
      ),
      child: Row(
        children: <Widget>[
          Icon(
            isPhoto ? Icons.photo_camera_outlined : Icons.image_outlined,
            size: 18,
            color: const Color(0xB3FFFFFF),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  asset.fileName.isEmpty ? '未命名' : asset.fileName,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 12.5, color: Colors.white),
                ),
                const SizedBox(height: 3),
                Text(
                  '${isPhoto ? '拍照' : '相册'} · ${_formatSize(asset.sizeBytes)} · ${asset.createdAt}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 11, color: Color(0x80FFFFFF)),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  static String _formatSize(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    return '${(bytes / 1024 / 1024).toStringAsFixed(2)} MB';
  }
}
