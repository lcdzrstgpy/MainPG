import 'package:flutter/foundation.dart';

import '../../../core/api/lan_api.dart';
import '../../../core/error/error_mapper.dart';
import '../../../core/http/api_client.dart';
import '../../../core/http/lan_client.dart';
import '../../../core/storage/token_store.dart';

/// 已登记的 PC 工作台（局域网通道）。
///
/// PC 端没有配对码 / 设备发现接口，因此设备由用户手动录入 `地址`，
/// [online] 完全来自真实探活（`GET /api/customer/me`），不做任何模拟。
class BoundDevice {
  const BoundDevice({
    required this.id,
    required this.name,
    required this.baseUrl,
    this.online = false,
    this.probing = false,
  });

  final String id;
  final String name;

  /// 规范化基址，形如 `http://192.168.1.24:8010`
  final String baseUrl;
  final bool online;
  final bool probing;

  /// 形如 `192.168.1.24:8010`
  String get host => baseUrl.replaceFirst(RegExp(r'^https?://'), '');

  Map<String, dynamic> toJson() => <String, dynamic>{
        'id': id,
        'name': name,
        'baseUrl': baseUrl,
      };

  factory BoundDevice.fromJson(Map<String, dynamic> json) {
    return BoundDevice(
      id: json['id']?.toString() ?? '',
      name: json['name']?.toString() ?? '',
      baseUrl: json['baseUrl']?.toString() ?? '',
    );
  }

  BoundDevice copyWith({String? name, String? baseUrl, bool? online, bool? probing}) {
    return BoundDevice(
      id: id,
      name: name ?? this.name,
      baseUrl: baseUrl ?? this.baseUrl,
      online: online ?? this.online,
      probing: probing ?? this.probing,
    );
  }
}

/// 多设备管理：登记列表 + 当前设备切换 + 局域网真实连通。
///
/// 通道切换规则：
/// - 当前设备探活成功 **且** 已持有局域网 token → 走局域网通道
/// - 否则清空 [LanClient] 基址 → 上层回落云端通道（账号 / 充值 / 消息）
class DeviceStore extends ChangeNotifier {
  DeviceStore(this._store, this._api, this._lan);

  final TokenStore _store;
  final LanApi _api;
  final LanClient _lan;

  final List<BoundDevice> _devices = <BoundDevice>[];
  String _currentId = '';
  String? _busyId;
  bool probing = false;
  String? error;

  List<BoundDevice> get devices => List<BoundDevice>.unmodifiable(_devices);

  bool get hasDevices => _devices.isNotEmpty;

  int get deviceCount => _devices.length;

  bool get isProbing => probing;

  String? get busyId => _busyId;

  bool isBusy(String id) => _busyId == id;

  bool get hasLanToken => (_store.lanToken ?? '').isNotEmpty;

  BoundDevice? get current {
    for (final BoundDevice device in _devices) {
      if (device.id == _currentId) return device;
    }
    return _devices.isEmpty ? null : _devices.first;
  }

  bool isCurrent(String id) => _currentId == id;

  /// 局域网通道可用 = 当前设备在线 + 已登录 PC 工作台。
  bool get lanAvailable => (current?.online ?? false) && hasLanToken;

  String get channelLabel => lanAvailable ? '局域网通道' : '云端通道';

  /// 冷启动：读本地登记 + 探活一次。
  Future<void> init() async {
    _devices
      ..clear()
      ..addAll(
        _store.devices
            .map(BoundDevice.fromJson)
            .where((BoundDevice device) => device.baseUrl.isNotEmpty),
      );
    _currentId = _store.currentDeviceId;
    if ((_currentId.isEmpty || current == null) && _devices.isNotEmpty) {
      _currentId = _devices.first.id;
    }
    _applyToLan();
    notifyListeners();
    await probeAll();
  }

  /// 登记一台 PC。返回用户可读错误文案，成功返回 null。
  Future<String?> addDevice({
    required String name,
    required String address,
  }) async {
    final String baseUrl = LanApi.normalizeBaseUrl(address);
    if (baseUrl.isEmpty) {
      return '地址格式不正确，请填写形如 192.168.1.24 的局域网地址';
    }
    if (_devices.any((BoundDevice device) => device.baseUrl == baseUrl)) {
      return '该地址已登记';
    }
    final String trimmedName = name.trim();
    final BoundDevice device = BoundDevice(
      id: 'd${DateTime.now().millisecondsSinceEpoch}',
      name: trimmedName.isEmpty ? baseUrl : trimmedName,
      baseUrl: baseUrl,
    );
    _devices.add(device);
    if (_currentId.isEmpty) _currentId = device.id;
    await _persist();
    notifyListeners();
    await probe(device.id);
    return null;
  }

  Future<void> removeDevice(String id) async {
    _devices.removeWhere((BoundDevice device) => device.id == id);
    if (_currentId == id) {
      _currentId = _devices.isEmpty ? '' : _devices.first.id;
    }
    await _persist();
    _applyToLan();
    notifyListeners();
  }

  /// 切换当前设备：先探活，再落盘并切换通道。返回是否发生切换。
  Future<bool> switchTo(String id) async {
    if (_currentId == id) return false;
    final int index = _devices.indexWhere((BoundDevice device) => device.id == id);
    if (index < 0) return false;

    _busyId = id;
    notifyListeners();

    final bool online = await _api.probe(
      baseUrl: _devices[index].baseUrl,
      token: _store.lanToken,
    );
    _devices[index] = _devices[index].copyWith(online: online);
    _currentId = id;
    await _persist();
    _applyToLan();
    _busyId = null;
    notifyListeners();
    return true;
  }

  /// 单台探活。
  Future<void> probe(String id) async {
    final int index = _devices.indexWhere((BoundDevice device) => device.id == id);
    if (index < 0) return;
    _devices[index] = _devices[index].copyWith(probing: true);
    notifyListeners();
    final bool online = await _api.probe(
      baseUrl: _devices[index].baseUrl,
      token: _store.lanToken,
    );
    _devices[index] = _devices[index].copyWith(online: online, probing: false);
    if (_currentId == id) _applyToLan();
    notifyListeners();
  }

  /// 全部设备探活（设置页 / 进入设备页时调用）。
  Future<void> probeAll() async {
    if (_devices.isEmpty) return;
    probing = true;
    notifyListeners();
    for (int i = 0; i < _devices.length; i++) {
      final bool online = await _api.probe(
        baseUrl: _devices[i].baseUrl,
        token: _store.lanToken,
      );
      _devices[i] = _devices[i].copyWith(online: online, probing: false);
    }
    probing = false;
    _applyToLan();
    notifyListeners();
  }

  /// 用云端账号登录当前 PC 工作台，换取局域网 token。
  ///
  /// 注意：PC 端为单端登录，此操作会把 PC 上的登录态挤掉（用户已确认接受）。
  Future<String?> loginLan({
    required String identifier,
    required String password,
  }) async {
    final BoundDevice? device = current;
    if (device == null) return '请先登记一台 PC 工作台';
    try {
      final String token = await _api.login(
        baseUrl: device.baseUrl,
        identifier: identifier,
        password: password,
      );
      if (token.isEmpty) return 'PC 端未返回有效令牌';
      await _store.saveLanToken(token);
      _applyToLan();
      await probe(device.id);
      notifyListeners();
      return null;
    } on ApiException catch (e) {
      return ErrorMapper.toUserMessage(e);
    } catch (_) {
      return '无法连接 PC 工作台，请确认手机与 PC 在同一局域网、地址填写正确';
    }
  }

  Future<void> logoutLan() async {
    await _store.saveLanToken('');
    _applyToLan();
    notifyListeners();
  }

  Future<void> _persist() async {
    await _store.saveDevices(
      _devices.map((BoundDevice device) => device.toJson()).toList(),
      currentId: _currentId,
    );
  }

  void _applyToLan() {
    final BoundDevice? device = current;
    if (device == null || !device.online) {
      _lan.useBaseUrl('');
      return;
    }
    _lan.useBaseUrl(device.baseUrl);
    _lan.setToken(_store.lanToken);
  }
}
