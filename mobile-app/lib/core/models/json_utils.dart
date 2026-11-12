/// JSON 取值兜底工具：后端字段可能缺失、为 null 或类型不符，
/// 统一在此做归一化，模型 `fromJson` 不再各自 `try/catch`。
library;

int asInt(dynamic value, [int fallback = 0]) {
  if (value is int) return value;
  if (value is double) return value.round();
  if (value is bool) return value ? 1 : 0;
  if (value is String) {
    final int? parsed = int.tryParse(value.trim());
    if (parsed != null) return parsed;
    final double? asDouble = double.tryParse(value.trim());
    if (asDouble != null) return asDouble.round();
  }
  return fallback;
}

double asDouble(dynamic value, [double fallback = 0]) {
  if (value is double) return value;
  if (value is int) return value.toDouble();
  if (value is String) {
    final double? parsed = double.tryParse(value.trim());
    if (parsed != null) return parsed;
  }
  return fallback;
}

String asString(dynamic value, [String fallback = '']) {
  if (value == null) return fallback;
  final String text = value.toString().trim();
  return text.isEmpty ? fallback : text;
}

bool asBool(dynamic value, [bool fallback = false]) {
  if (value is bool) return value;
  if (value is num) return value != 0;
  if (value is String) {
    final String text = value.trim().toLowerCase();
    if (text == 'true' || text == '1' || text == 'yes') return true;
    if (text == 'false' || text == '0' || text == 'no') return false;
  }
  return fallback;
}

Map<String, dynamic> asMap(dynamic value) {
  if (value is Map) return value.cast<String, dynamic>();
  return const <String, dynamic>{};
}

List<dynamic> asList(dynamic value) {
  if (value is List) return value;
  return const <dynamic>[];
}

/// 分（cents）转「元」展示文案：10000 → `100`，5050 → `50.5`
String formatYuan(int cents) {
  if (cents % 100 == 0) return '${cents ~/ 100}';
  final String raw = (cents / 100).toStringAsFixed(2);
  return raw.replaceFirst(RegExp(r'0+$'), '').replaceFirst(RegExp(r'\.$'), '');
}
