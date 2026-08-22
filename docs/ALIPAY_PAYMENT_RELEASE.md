# 支付宝积分充值：合并与安装包交接

## 本次能力

- 个人中心仅开放支付宝电脑网站支付；微信支付保留为未开放状态。
- 用户选择固定积分包或输入 `1` 至 `3000` 元的整数自定义金额后，服务器创建待支付订单并生成支付宝跳转链接。
- 支付宝异步通知会校验 RSA2 签名、应用 ID、商户号（已配置时）、订单号、支付金额和交易状态。
- 验签通过后，订单状态、积分钱包和积分账本在同一 SQLite 事务中更新。重复通知不会重复加积分。
- 支付完成后的浏览器会回到本机工作台：`http://127.0.0.1:8010/?module=personal_center&payment=success`，客户端刷新余额与订单列表。

## 合并后服务器必须具备

支付订单、余额和账本以平台账号服务为准，不保存在桌面端本地数据库。先部署账号服务，再发布桌面安装包。

1. 账号服务使用包含 `local-runtime/wh_local/customer/alipay_gateway.py`、`auth_server.py` 与 `billing.py` 的版本，并重启 `wh-customer-auth.service`。
2. Nginx 的 `/auth-api/` 继续转发到账号服务；支付宝异步通知地址必须可从公网访问：
   `https://workbench.haocoming.top/auth-api/api/customer/billing/payment-callback/alipay`
3. 服务器私密文件 `/etc/wh-workbench/alipay.env` 需要配置下列变量，且由服务的 systemd 环境加载：

```ini
ALIPAY_APP_ID=
ALIPAY_SELLER_ID=
ALIPAY_GATEWAY=https://openapi.alipay.com/gateway.do
ALIPAY_NOTIFY_URL=https://workbench.haocoming.top/auth-api/api/customer/billing/payment-callback/alipay
ALIPAY_RETURN_URL=https://workbench.haocoming.top/auth-api/api/customer/billing/payment-return
ALIPAY_LOCAL_RETURN_URL=http://127.0.0.1:8010/?module=personal_center&payment=success
ALIPAY_MERCHANT_PRIVATE_KEY_PATH=/etc/wh-workbench/keys/alipay_app_private_key.pem
ALIPAY_PUBLIC_KEY_PATH=/etc/wh-workbench/keys/alipay_public_key.pem
```

私钥、支付宝公钥、生产数据库和任何环境文件都只留在服务器，不能提交 Git，也不能打进安装包。

## 桌面安装包应包含

1. 本次最新构建的 `web-frontend/dist`，其中必须包含个人中心的支付宝选择、自定义金额、创建订单、跳转和回跳刷新逻辑。
2. 与该前端匹配的本地运行时及启动器；正式安装版应在本机 `127.0.0.1:8010` 提供工作台页面。
3. 现有平台账号 API 配置，桌面端请求仍需指向 `https://workbench.haocoming.top/auth-api`，而非打到自己的本地 SQLite 余额。

不要打包：`alipay.env`、PEM 密钥、服务器 SQLite 文件、测试订单、`.venv`、`node_modules`、浏览器缓存和临时构建文件。

## 发布前验证

1. 安装全新的桌面安装包，登录一个测试账号，进入个人中心。
2. 选择支付宝，分别验证固定金额和 `1` 至 `3000` 元整数自定义金额；微信按钮应不可创建订单。
3. 使用支付宝沙箱或经批准的小额真实支付完成一笔订单。
4. 支付后应回到本机个人中心；刷新后“可用积分”和“最近订单”均显示服务端的新结果。
5. 重复刷新、重复打开回跳页或重复通知不得再次增加积分。

## 用户链路

`登录桌面端 -> 个人中心选择支付宝与金额 -> 服务器创建 pending 订单 -> 浏览器跳转支付宝 -> 支付宝验签通知服务器 -> 同事务更新订单/钱包/账本 -> 返回本机工作台并刷新积分`
