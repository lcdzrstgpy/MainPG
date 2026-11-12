/// 全局配置（与 PC 端 local-runtime/wh_local/config.py 对齐）
class AppConfig {
  AppConfig._();

  /// 版本基线，计费类请求会随代理自动注入 app_version
  static const String appVersion = '1.5.4';

  /// 通道 A：云端直连（免 PC）
  static const String authBaseUrl = 'https://workbench.haocoming.top/auth-api';
  static const String publishBaseUrl = 'https://workbench.haocoming.top/publish-api';

  /// 通道 B：局域网直连 PC 工作台，配对成功后由 LanClient.useBaseUrl 覆盖
  static const String defaultLanBaseUrl = '';

  /// PC 局域网默认端口
  static const int lanDefaultPort = 8010;

  static const Duration requestTimeout = Duration(seconds: 30);

  /// publish-api（公告 / 反馈回复）的身份头
  static const String publishTokenHeader = 'x-auth-token';

  // ---------------------------------------------------------------- auth-api
  static const String accountLoginPath = '/api/customer/login';
  static const String accountLogoutPath = '/api/customer/logout';
  static const String accountMePath = '/api/customer/me';
  static const String accountChangePasswordPath = '/api/customer/change-password';
  static const String accountRegisterPath = '/api/customer/register';
  static const String accountEmailCodePath = '/api/customer/email-code';

  static const String billingSummaryPath = '/api/customer/billing/summary';
  static const String billingLedgerPath = '/api/customer/billing/ledger';
  static const String billingUsagePath = '/api/customer/billing/usage';
  static const String billingTopupOrdersPath = '/api/customer/billing/topup-orders';
  static const String billingTopupQuotePath = '/api/customer/billing/topup-quote';
  static const String billingClaimBasicPath =
      '/api/customer/billing/plan-basic/claim';
  static const String billingClaimDailyPath =
      '/api/customer/billing/daily-extra/claim';

  static const String feedbackPath = '/api/customer/feedback';
  static const String feedbackMinePath = '/api/customer/feedback/mine';

  /// 手机互通资产（拍照 / 相册上传，账号维度，PC 端同账号可见）
  static const String accountAssetsPath = '/api/customer/assets';

  /// 手机采集链接（手机登记后上传云端，PC 端「手机端导入」拉取）
  static const String accountCapturesPath = '/api/customer/captures';

  // ---------------------------------------------------------------- publish-api
  static const String publicAnnouncementsPath = '/api/announcements/public';
  static const String publicFeedbackRepliesPath = '/api/feedback-replies/public';

  static String announcementImagesPath(int id) => '/api/announcements/$id/images';

  // ---------------------------------------------------------------- 局域网（PC）
  /// 与云端同契约：本地 `/api/customer/*` 是云端 customer-auth 的透明代理
  static const String lanLoginPath = '/api/customer/login';
  static const String lanHealthPath = '/api/customer/me';
}
