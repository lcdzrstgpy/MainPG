# MainPG 更新发布后台

这是独立于公告后台的更新发布服务。管理员只需上传 Windows `.exe` 安装包、填写版本号和更新说明，后台会自动调用 EV Sign 为外层安装包添加 Authenticode 签名，验证签名后再完成 SHA-256、Ed25519 清单签名、历史归档和原子发布。构建脚本会把新旧版本的文件差异嵌入同一个 EXE；后台逐文件复算哈希后，再自动生成并签署公开的文件级增量补丁。

## 首次启动

1. 创建独立 Python 虚拟环境并安装 `requirements.txt`。
2. 按 `.env.example` 配置环境变量。真实密码和 Ed25519 私钥必须放在服务器私密配置中，不能写进源码或网页目录。
3. 使用工厂模式启动：

   ```powershell
   uvicorn app:app_factory --factory --host 127.0.0.1 --port 8791
   ```

首次初始化会创建 `He123`、`Liu123`、`Dai123`、`Yang123`、`Shen123` 和 `boss`。前五个账号首次登录必须修改密码；`boss` 视为已经完成首次改密。密码只在初始化时读取，数据库仅保存 Argon2id 哈希。

## 发布流程

1. 用构建脚本生成新的 `MainPG-Setup-<version>.exe`。
2. 登录后台，直接选择该 EXE。
3. 填写 SemVer 版本号（如 `1.3.4`）、更新说明、是否强制更新。
4. 点击“上传、签名并发布”。

后台先验证 EXE 文件头和版本递增，再读取 EXE 尾部的补丁描述和变更文件，逐个核对路径、体积与 SHA-256。随后把完整安装包提交到 EV Sign。只有签名后的文件通过 Authenticode 校验，后台才会签署完整更新清单和补丁清单并原子发布。客户端版本与补丁起点不一致或补丁生成失败时，仍使用完整安装包。每个安装包只长期保存一份，临时补丁文件在请求结束后删除。

## 生产部署注意

- `UPDATE_PUBLISH_DIR` 应指向官网 `/mainpg/windows` 对应的静态目录，Nginx 只读提供下载。
- 后台部署在 `/update-admin/` 这类子路径时，把 `UPDATE_ADMIN_COOKIE_PATH` 设置为 `/update-admin`。
- 更新后台应放在独立域名或独立受限路径，强制 HTTPS，不与普通用户后台共用会话。
- Ed25519 私钥必须位于网页目录之外，并限制为服务账户可读。
- `UPDATE_EXPECTED_PUBLIC_KEY_B64` 必须与客户端内置公钥一致，否则后台会拒绝发布。
- `EVSIGN_LICENSE_KEY` 只能放在服务器私密环境文件中，不能进入源码、网页、数据库或日志。设置 `EVSIGN_REQUIRED=1` 可禁止绕过自动签名。
- Linux 服务器应安装 `osslsigncode`，并设置 `UPDATE_REQUIRE_AUTHENTICODE=1`，否则后台会拒绝发布无法验证的安装包。
- `innoextract` 仅作为旧安装包兼容回退；新版构建不依赖服务器理解 Inno Setup 的内部压缩格式。补丁文件先完整发布，最后才原子替换 `patch-manifest.json`，避免客户端读到不完整补丁。
- EV Sign API 会接收完整安装包。若不能接受第三方处理文件，应改用自有 OV/EV 证书。
- 建议由反向代理限制上传体积，并备份数据库、`releases` 历史目录和当前 `manifest.json`。

## 测试

```powershell
pytest -q
```

仓库内的 `deploy/mainpg-update-admin.service` 是生产 systemd 模板，默认监听服务器回环地址 `127.0.0.1:8013`，只通过 Nginx 的 HTTPS `/update-admin/` 路径访问。
