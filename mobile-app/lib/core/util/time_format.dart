/// 时间展示工具。
///
/// 后端时间统一为北京时间 ISO 8601（形如 `2026-10-08T09:32:14+08:00`），
/// 这里按「原样切片」展示，不做时区换算，避免设备时区导致的时间漂移；
/// 解析失败时退回原串，保证不崩。
library;

String _date(String iso) =>
    iso.length >= 10 ? iso.substring(0, 10) : iso;

String _hm(String iso) =>
    iso.length >= 16 ? iso.substring(11, 16) : '';

/// `2026-10-08 09:32`
String formatDateTime(String iso) {
  if (iso.isEmpty) return '—';
  final String hm = _hm(iso);
  return hm.isEmpty ? iso : '${_date(iso)} $hm';
}

/// `10-08 09:32`
String formatShort(String iso) {
  if (iso.isEmpty) return '—';
  final String hm = _hm(iso);
  final String md = iso.length >= 10 ? iso.substring(5, 10) : iso;
  return hm.isEmpty ? md : '$md $hm';
}

/// `2026-10`
String formatMonth(String iso) =>
    iso.length >= 7 ? iso.substring(0, 7) : (iso.isEmpty ? '—' : iso);

/// 当月第几天（解析失败返回 0）
int dayOfMonth(String iso) {
  if (iso.length < 10) return 0;
  return int.tryParse(iso.substring(8, 10)) ?? 0;
}
