# 腾讯云 SES 邮箱验证码配置

邮箱验证码必须由远端 `customer-auth` 服务发送。本地桌面客户端只调用
`/api/customer/email-code`，不得保存或接触腾讯云密钥。

## 腾讯云资源

- 地域：`ap-hongkong`
- 发信地址：`verify@notice.haocoming.top`
- 模板 ID：`212484`
- 模板变量：`{{code}}`

建议给服务创建仅用于编程访问的 CAM 子用户，并只授予 `ses:SendEmail`：

```json
{
  "version": "2.0",
  "statement": [
    {
      "effect": "allow",
      "action": ["ses:SendEmail"],
      "resource": ["*"]
    }
  ]
}
```

## 服务器环境变量

```text
TENCENTCLOUD_SECRET_ID=<CAM 子用户 SecretId>
TENCENTCLOUD_SECRET_KEY=<CAM 子用户 SecretKey>
TENCENTCLOUD_SES_REGION=ap-hongkong
TENCENTCLOUD_SES_FROM_EMAIL=verify@notice.haocoming.top
TENCENTCLOUD_SES_TEMPLATE_ID=212484
TENCENTCLOUD_SES_DISPLAY_NAME=界野
TENCENTCLOUD_SES_SUBJECT=界野邮箱验证码
WH_EMAIL_CODE_SECRET=<独立生成的至少 32 字符随机密钥>
```

`WH_EMAIL_CODE_SECRET` 用于 HMAC 保护数据库中的六位验证码摘要，它不能与腾讯云
SecretKey 共用。以上两个 Secret 值不要写入 Git、前端环境变量或安装包。

生成独立验证码密钥的示例：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

安装新增依赖并重启远端认证服务：

```powershell
python -m pip install -r requirements.txt
```

## HTTP 契约

发送注册验证码：

```json
POST /api/customer/email-code
{"email":"name@example.com","purpose":"register"}
```

注册时提交验证码：

```json
POST /api/customer/register
{
  "username":"example",
  "email":"name@example.com",
  "email_code":"123456",
  "password":"example-password",
  "invitation_code":"example-invitation"
}
```

验证码有效期 10 分钟，同一邮箱 60 秒后才能重发，每小时最多发送 5 次，最多允许
连续输错 5 次。数据库只保存带服务端密钥的 HMAC 摘要，不保存验证码明文。

### 忘记密码（重置密码验证码）

发送重置密码验证码（只发到已注册账号的邮箱，未注册邮箱返回同一话术）：

```json
POST /api/customer/forgot-password
{"email":"name@example.com"}
```

使用验证码重置密码：

```json
POST /api/customer/reset-password
{
  "email":"name@example.com",
  "code":"123456",
  "new_password":"new-password"
}
```

忘记密码与注册共用同一套验证码表（`purpose` 分别为 `register` /
`reset_password`），规则一致：10 分钟有效、60 秒重发冷却、每小时 5 次、错 5 次作废。
`forgot-password` 不再返回 reset_token。
