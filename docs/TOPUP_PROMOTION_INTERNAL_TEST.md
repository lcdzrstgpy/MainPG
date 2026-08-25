# 充值翻倍活动：本地内测操作

本说明仅用于本地内测账号服务。活动配置保存在账号服务使用的 SQLite 数据库中；不需要修改支付宝密钥或重启服务。

## 活动控制

在 `local-runtime` 目录执行。未传入 `--database` 时，命令使用本地账号服务默认数据库；内测建议显式传入测试数据库路径，避免影响其他本地数据。

```powershell
python manage_topup_promotion.py status --database .\data\customer-auth.sqlite3
python manage_topup_promotion.py enable --database .\data\customer-auth.sqlite3
python manage_topup_promotion.py disable --database .\data\customer-auth.sqlite3
```

- 初始状态为关闭。
- `enable` 后，新建的固定套餐和自定义金额订单均按双倍积分结算。
- `disable` 只影响之后新建的订单。已创建的 pending 订单保留建单时的活动快照，支付成功后仍按快照入账。
- `status` 只输出活动状态和公开活动信息，不输出数据库路径以外的账户、密钥或支付配置。

## 内测验收

1. 使用 `enable` 开启活动；调用账单摘要接口或启动已连接该测试账号服务的桌面端，确认固定档为 50、99、199、499、999 元，且每档显示基础、活动赠送和合计积分。
2. 分别创建五档固定订单及一笔合法整数自定义金额订单；确认每笔 `total_points = base_points * 2`，`promotion_bonus_points = base_points`。
3. 创建一笔 pending 订单后执行 `disable`；再创建一笔新订单。前者支付结算后仍翻倍，后者只入基础积分。
4. 对同一已支付订单重复通知或重复刷新；确认钱包余额不再增加，账本仅有一条 `payment_alipay` 与一条 `topup_promotion_bonus`。
5. 执行 `status` 确认状态；结束内测后执行 `disable`，避免下一轮测试误用活动。

## 边界

- 当前活动只控制积分翻倍，不影响支付金额、支付宝验签、订单金额校验或订单 30 分钟有效期。
- 活动替代首充赠送：新订单不会产生 `first_topup_bonus` 流水。
- 正式服务器部署另行安排；本地内测不得把密钥、生产数据库或服务器环境文件复制到仓库。
