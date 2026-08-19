# MainPG 1.0.2-bata Windows 内测包设计

## 目标

为当前项目生成第一版 Windows x64 内测产物：

- `MainPG-Setup-1.0.2-bata.exe`：面向普通内测用户的安装程序；
- `MainPG-portable-1.0.2-bata.zip`：无需安装的便携包；
- `SHA256SUMS.txt`：两个产物的 SHA-256 校验值。

本次仅建立可重复的 Windows 构建与验证流程，不调整现有业务、安全、计费、API 密钥或万邦/OneBound 调用逻辑。内测安装程序不做 Windows 代码签名，用户可能看到 SmartScreen 的“未知发布者”提示。

## 构建架构

使用 GitHub Actions 的 `windows-latest` 托管运行器执行手动触发的内测构建。工作流固定从触发时选定的提交构建，版本默认且仅允许使用合法 SemVer；本次输入为 `1.0.2-bata`。

构建链沿用项目现有实现：

1. 安装 Python 3.11、Node.js 和项目依赖；
2. 执行前端 TypeScript 检查与 Vite 构建；
3. 执行 `local-runtime/build_installer.ps1 -Version 1.0.2-bata`；
4. 由 PyInstaller 生成 `dist/MainPG` onedir 应用；
5. 由 PowerShell 压缩便携版 ZIP；
6. 由 Inno Setup 生成版本化 Setup EXE；
7. 生成统一 SHA-256 校验文件；
8. 将 EXE、ZIP 和校验文件作为同一个 GitHub Actions artifact 上传。

不创建 GitHub Release，不生成正式自动更新 manifest，也不要求发布签名私钥。

## 安全边界

构建不引入新的密钥托管、代理或加密机制，严格保持当前项目行为。工作流不读取或注入第三方 API 密钥，也不声明相关 GitHub Actions secrets。

现有 `build_installer.ps1` 的敏感文件门禁继续生效，打包目录中不得出现：

- `.env`；
- `cos.local.json`；
- `onebound.local.json`；
- `workbench.sqlite3`；
- 任意 `.sqlite`、`.sqlite3` 或 `.db` 文件。

若发现上述文件，构建必须失败，不上传产物。万邦/OneBound 与 AI 调用方式均保持当前实现，不在本次改动范围内。

## 运行与数据流

用户安装或解压后运行 `MainPG.exe`。程序启动本地 FastAPI/uvicorn 服务，默认监听 `127.0.0.1:8010`，并打开本机浏览器访问工作台。应用数据继续写入用户可写的 `%APPDATA%\MainPG`，不写入安装目录。

客户端与远端账号、计费和 AI 服务的通信路径保持现有实现。构建系统只负责把当前源码与静态前端资源封装为 Windows 应用，不改变请求目标或认证流程。

## 验证策略

工作流依次执行以下验证：

1. 前端 `npm run build` 成功；
2. Windows 发行、更新与安装脚本相关测试通过；
3. PyInstaller 输出目录存在且敏感文件扫描通过；
4. 便携版 `MainPG.exe --no-browser` 能启动；
5. `http://127.0.0.1:8010/health` 在限定时间内返回成功；
6. Setup EXE 可静默安装到临时目录；
7. 安装后的 `MainPG.exe --no-browser` 能启动并通过同一健康检查；
8. EXE、ZIP 和 `SHA256SUMS.txt` 均存在且文件名版本一致。

每个启动验证后都必须结束对应 `MainPG.exe`，避免端口或文件占用影响后续步骤。

## 失败处理

- 依赖安装、前端构建、测试、PyInstaller 或 Inno Setup 任一步失败，工作流立即失败；
- 健康检查超时或返回非成功状态，工作流失败并上传构建日志，但不把未验证包标记为可下载内测产物；
- 安装程序或便携包缺失、版本名不一致、校验文件缺失，工作流失败；
- 敏感文件扫描命中时立即失败，且不得上传任何应用包。

## 交付与验收

一次成功的 `1.0.2-bata` 工作流运行必须提供可下载 artifact，内含：

```text
MainPG-Setup-1.0.2-bata.exe
MainPG-portable-1.0.2-bata.zip
SHA256SUMS.txt
```

验收以 GitHub Actions Windows 构建成功、便携版和安装版健康检查通过、产物敏感文件扫描通过为准。首版为无代码签名内测包，SmartScreen 提示属于已接受限制。
