import 'dart:convert';
import 'dart:typed_data';

import '../config/app_config.dart';
import '../http/cloud_client.dart';

/// 一条手机互通资产（元数据，不含原图数据）。
class MobileAsset {
  const MobileAsset({
    required this.assetId,
    required this.assetType,
    required this.fileName,
    required this.mime,
    required this.sizeBytes,
    required this.createdAt,
  });

  final String assetId;
  final String assetType;
  final String fileName;
  final String mime;
  final int sizeBytes;
  final String createdAt;

  factory MobileAsset.fromJson(Map<String, dynamic> json) {
    return MobileAsset(
      assetId: json['asset_id']?.toString() ?? '',
      assetType: json['asset_type']?.toString() ?? 'photo',
      fileName: json['file_name']?.toString() ?? '',
      mime: json['mime']?.toString() ?? '',
      sizeBytes: int.tryParse(json['size_bytes']?.toString() ?? '') ?? 0,
      createdAt: json['created_at']?.toString() ?? '',
    );
  }
}

/// 手机互通资产服务（auth-api `/api/customer/assets`）。
///
/// 图片以 base64 JSON body 上传（与 PC 端日志上传 / 反馈接口同惯例），
/// 云端按账号维度落库；PC 端用同一账号登录即可看到，因此手机注册的账号
/// 在 PC 端登录后也能取用这里上传的图片。
class AssetApi {
  AssetApi(this._cloud);

  final CloudClient _cloud;

  /// 上传一张图片到云端（账号维度）。
  ///
  /// [assetType] 取 `photo`（拍照）或 `gallery`（相册）。
  Future<Map<String, dynamic>> upload({
    required Uint8List bytes,
    required String fileName,
    required String mime,
    String assetType = 'photo',
    String note = '',
  }) {
    return _cloud.auth.post(
      AppConfig.accountAssetsPath,
      body: <String, dynamic>{
        'data_b64': base64Encode(bytes),
        'file_name': fileName,
        'mime': mime,
        'asset_type': assetType,
        'client_type': 'mobile',
        'platform': 'android',
        if (note.trim().isNotEmpty) 'note': note.trim(),
      },
    );
  }

  /// 拉取当前账号的资产元数据列表（不含原图数据）。
  Future<List<MobileAsset>> list({int limit = 48, int offset = 0}) async {
    final Map<String, dynamic> data = await _cloud.auth.get(
      AppConfig.accountAssetsPath,
      query: <String, dynamic>{'limit': limit, 'offset': offset},
    );
    final List<dynamic> raw = (data['assets'] as List?) ?? const <dynamic>[];
    return raw
        .whereType<Map>()
        .map((Map item) => MobileAsset.fromJson(item.cast<String, dynamic>()))
        .toList(growable: false);
  }
}
