import '../config/app_config.dart';
import '../http/cloud_client.dart';

/// 手机采集链接的云端回执。
class CloudCapture {
  const CloudCapture({required this.captureId, required this.createdAt});

  final String captureId;
  final String createdAt;
}

/// 手机采集链接服务（auth-api `/api/customer/captures`）。
///
/// 手机端「采集」登记的商品链接会上传到云端（账号维度），PC 端用同一账号登录后
/// 即可在采集页「手机端导入」里拉到并一键导入采集流程。
/// capture_id 由手机端生成并回传，云端按 (账号, capture_id) 幂等写入，重试不会重复。
class CaptureApi {
  CaptureApi(this._cloud);

  final CloudClient _cloud;

  Future<CloudCapture> submit({
    required String captureId,
    required String sourceUrl,
    required String sourceSite,
    String title = '',
    String note = '',
    String deviceName = '',
  }) async {
    final Map<String, dynamic> data = await _cloud.auth.post(
      AppConfig.accountCapturesPath,
      body: <String, dynamic>{
        'capture_id': captureId,
        'source_url': sourceUrl,
        'source_site': sourceSite,
        'client_type': 'mobile',
        'platform': 'android',
        'app_version': AppConfig.appVersion,
        if (title.trim().isNotEmpty) 'title': title.trim(),
        if (note.trim().isNotEmpty) 'note': note.trim(),
        if (deviceName.trim().isNotEmpty) 'device_name': deviceName.trim(),
      },
    );
    return CloudCapture(
      captureId: data['capture_id']?.toString() ?? captureId,
      createdAt: data['created_at']?.toString() ?? '',
    );
  }

  /// 删除云端这条采集登记（手机端删除本地记录时尽力同步）。
  Future<void> delete(String captureId) {
    return _cloud.auth
        .delete('${AppConfig.accountCapturesPath}/$captureId');
  }
}
