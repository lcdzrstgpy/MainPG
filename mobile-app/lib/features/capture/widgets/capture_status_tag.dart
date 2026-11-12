import 'package:flutter/material.dart';

import '../../../shared/widgets/app_blocks.dart';
import '../state/capture_store.dart';

/// 采集状态标签：待 PC 导入 / 未同步 / 登记失败
class CaptureStatusTag extends StatelessWidget {
  const CaptureStatusTag({super.key, required this.status, this.withIcon = true});

  final CaptureStatus status;
  final bool withIcon;

  @override
  Widget build(BuildContext context) {
    final (AppTagTone, IconData) conf = switch (status) {
      CaptureStatus.synced => (AppTagTone.brand, Icons.cloud_done_outlined),
      CaptureStatus.pendingSync => (AppTagTone.warn, Icons.cloud_off_outlined),
      CaptureStatus.failed => (AppTagTone.danger, Icons.error_outline),
    };
    return AppTag(
      status.label,
      tone: conf.$1,
      icon: withIcon ? conf.$2 : null,
    );
  }
}
