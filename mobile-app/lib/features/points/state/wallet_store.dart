import 'dart:math';

import 'package:flutter/foundation.dart';

import '../../../core/api/billing_api.dart';
import '../../../core/error/error_mapper.dart';
import '../../../core/http/api_client.dart';
import '../../../core/models/billing_models.dart';

/// 领取结果（签到 / 基础版每周领取）
class ClaimOutcome {
  const ClaimOutcome(this.ok, this.message);

  final bool ok;
  final String message;
}

/// 下单结果：成功带 [result]，失败带 [error] 文案。
class OrderOutcome {
  const OrderOutcome({this.result, this.error});

  final TopupOrderResult? result;
  final String? error;

  bool get ok => error == null && result != null;
}

/// 积分钱包状态：驱动首页 / 我的 / 积分中心 / 签到 / 流水 / 充值。
class WalletStore extends ChangeNotifier {
  WalletStore(this._api);

  final BillingApi _api;

  static const int _ledgerPageSize = 20;
  static const String signSourceType = 'daily_extra_claim';

  bool loading = false;
  bool loaded = false;
  String? error;
  BillingSummary? summary;

  // ---- 明细分页（流水页） ----
  final List<LedgerEntry> ledger = <LedgerEntry>[];
  bool ledgerLoading = false;
  bool ledgerHasMore = true;
  String? ledgerError;
  String _ledgerCategory = '';
  int _ledgerOffset = 0;

  // ---- 活动积分（签到页用，独立于分页明细） ----
  final List<LedgerEntry> rewardEntries = <LedgerEntry>[];
  bool rewardLoading = false;
  String? rewardError;

  int get points => summary?.displayPoints ?? 0;

  String get planLabel {
    final String label = summary?.planLabel ?? '';
    return label.isEmpty ? '—' : label;
  }

  String get planExpireAt => summary?.planExpireAt ?? '';

  bool get basicClaimable => summary?.basicClaimable ?? false;

  bool get dailyClaimable => summary?.dailyClaimable ?? false;

  int get dailyClaimPoints => summary?.dailyClaimPoints ?? 0;

  int get dailyFirstBonus => summary?.dailyFirstClaimBonus ?? 0;

  int get dailyWeekCount => summary?.dailyWeekCount ?? 0;

  int get weeklyMax => summary?.dailyWeekMax ?? 7;

  String get dailyNextClaimAt => summary?.dailyNextClaimAt ?? '';

  String get minClientVersion => summary?.pricing.minClientVersion ?? '';

  List<BillingPackage> get packages => summary?.packages ?? const <BillingPackage>[];

  List<LedgerEntry> get recentLedger =>
      summary?.recentLedger ?? const <LedgerEntry>[];

  List<BillingOrder> get recentOrders =>
      summary?.recentOrders ?? const <BillingOrder>[];

  BillingOrder? get pendingOrder => summary?.pendingOrder;

  /// 签到类明细（每日签到），供签到页统计连续天数与已签日期。
  List<LedgerEntry> get signEntries => rewardEntries
      .where((LedgerEntry e) => e.sourceType == signSourceType)
      .toList();

  /// 拉取钱包总览；返回是否成功。
  Future<bool> load() async {
    loading = true;
    error = null;
    notifyListeners();
    try {
      summary = await _api.summary();
      loaded = true;
      loading = false;
      notifyListeners();
      return true;
    } on ApiException catch (e) {
      error = ErrorMapper.toUserMessage(e);
    } catch (_) {
      error = '网络异常，请稍后重试';
    }
    loading = false;
    notifyListeners();
    return false;
  }

  Future<void> refresh() => load().then((_) {});

  /// 积分明细分页。`category` 变化时自动重置列表。
  Future<void> loadLedger({String? category, bool loadMore = false}) async {
    final String target = category ?? _ledgerCategory;
    if (target != _ledgerCategory) {
      _ledgerCategory = target;
      loadMore = false;
    }
    if (ledgerLoading) return;
    if (loadMore && !ledgerHasMore) return;

    if (!loadMore) {
      ledger.clear();
      _ledgerOffset = 0;
      ledgerHasMore = true;
    }
    ledgerLoading = true;
    ledgerError = null;
    notifyListeners();

    try {
      final BillingLedgerPage page = await _api.ledger(
        category: _ledgerCategory,
        limit: _ledgerPageSize,
        offset: _ledgerOffset,
      );
      ledger.addAll(page.items);
      _ledgerOffset += page.items.length;
      ledgerHasMore = page.hasMore && page.items.isNotEmpty;
    } on ApiException catch (e) {
      ledgerError = ErrorMapper.toUserMessage(e);
    } catch (_) {
      ledgerError = '网络异常，请稍后重试';
    }
    ledgerLoading = false;
    notifyListeners();
  }

  /// 活动积分明细（签到页统计用）。
  Future<void> loadRewardEntries({int limit = 50}) async {
    if (rewardLoading) return;
    rewardLoading = true;
    rewardError = null;
    notifyListeners();
    try {
      final BillingLedgerPage page =
          await _api.ledger(category: 'reward', limit: limit, offset: 0);
      rewardEntries
        ..clear()
        ..addAll(page.items);
    } on ApiException catch (e) {
      rewardError = ErrorMapper.toUserMessage(e);
    } catch (_) {
      rewardError = '网络异常，请稍后重试';
    }
    rewardLoading = false;
    notifyListeners();
  }

  Future<ClaimOutcome> claimBasic() => _claim(_api.claimBasic, '基础版每周积分');

  Future<ClaimOutcome> claimDaily() => _claim(_api.claimDaily, '每日签到');

  /// 创建充值订单。`idempotencyKey` 缺省时按 `idem_<millis>_<random6>` 生成。
  Future<OrderOutcome> createTopupOrder({
    required String packageId,
    String? idempotencyKey,
    int? amountCents,
  }) async {
    try {
      final TopupOrderResult result = await _api.topupOrder(
        packageId: packageId,
        idempotencyKey: idempotencyKey ?? _newIdempotencyKey(),
        amountCents: amountCents,
      );
      await load();
      return OrderOutcome(result: result);
    } on ApiException catch (e) {
      return OrderOutcome(error: ErrorMapper.toUserMessage(e));
    } catch (_) {
      return const OrderOutcome(error: '网络异常，请稍后重试');
    }
  }

  static final Random _random = Random();

  static String _newIdempotencyKey() {
    final int rand = _random.nextInt(1000000);
    return 'idem_${DateTime.now().millisecondsSinceEpoch}_'
        '${rand.toString().padLeft(6, '0')}';
  }

  Future<ClaimOutcome> _claim(
    Future<ClaimResult> Function() action,
    String label,
  ) async {
    try {
      final ClaimResult result = await action();
      await load();
      if (result.message.isNotEmpty) {
        return ClaimOutcome(true, result.message);
      }
      final String gained =
          result.claimedPoints > 0 ? '，＋${result.claimedPoints} 积分' : '';
      return ClaimOutcome(true, '$label成功$gained');
    } on ApiException catch (e) {
      return ClaimOutcome(false, ErrorMapper.toUserMessage(e));
    } catch (_) {
      return const ClaimOutcome(false, '网络异常，请稍后重试');
    }
  }

  void clear() {
    loading = false;
    loaded = false;
    error = null;
    summary = null;
    ledger.clear();
    ledgerLoading = false;
    ledgerHasMore = true;
    ledgerError = null;
    _ledgerCategory = '';
    _ledgerOffset = 0;
    rewardEntries.clear();
    rewardLoading = false;
    rewardError = null;
    notifyListeners();
  }
}
