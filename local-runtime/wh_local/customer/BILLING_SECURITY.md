# 个人中心、积分与支付安全边界

本模块涉及真实金额，默认安全模型是“服务器唯一可信，本地只展示和发起请求”。

## 信任边界

- 本地工作台不保存可信余额，不允许直接写积分。
- 浏览器 localStorage、本地 SQLite、前端状态都只能作为展示缓存，不能作为结算依据。
- 用户身份以平台账号服务端签发的 `wh_auth_*` session 为准。
- 积分钱包、支付订单、积分账本、扣费事件都存放在平台服务器数据库。

## 当前已实现的后端骨架

- `billing_wallets`：按 `account_id + workspace_id` 绑定的钱包余额。
- `billing_payment_orders`：微信/支付宝充值订单，创建后默认 `pending`。
- `billing_point_ledger`：积分变动追加账本，预留 `previous_hash + row_hash` hash 链。
- `billing_usage_events`：业务模块扣费事件，要求幂等键。
- `/api/customer/billing/summary`：读取服务器余额、套餐、订单、账本摘要。
- `/api/customer/billing/topup-orders`：创建充值订单，只返回 `pending`，不会本地入账。
- `/api/customer/billing/payment-callback/{provider}`：当前 fail closed。未配置官方验签/解密前不会入账。

## 微信/支付宝接入要求

生产接入时，支付回调必须完成以下校验后才能写账本：

1. 校验平台签名：微信支付 API v3 平台证书/序列号/时间戳/nonce，支付宝 RSA2 公钥验签。
2. 解密或解析回调资源。
3. 比对 `out_trade_no`、商户号、金额、币种、支付状态、订单归属账号。
4. 同一个 `out_trade_no` 幂等处理，重复回调不得重复加积分。
5. 在同一个数据库事务内更新订单状态、追加积分账本、更新钱包余额和账本 head hash。
6. 所有商户私钥、APIv3 key、支付宝应用私钥只能放服务器环境变量或服务器密钥文件，不能进入前端、本地配置或 Git。

## ECS 初始化建议

为避免 root 密码进入命令历史，先把部署公钥加入服务器：

```bash
mkdir -p /root/.ssh
chmod 700 /root/.ssh
printf '%s\n' '<PUBLIC_KEY>' >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
```

确认 key 登录可用后再初始化服务、数据库、HTTPS 与 systemd。
