# MainPG
A new environment

## AI 视频模块

ClipForge 的完整源码已直接纳入 `integrations/clipforge`；它不是 Git submodule，也不在运行时引用外部仓库。MainPG 的管理后端会在本机回环地址启动它，并在工作台的“AI 视频”入口中以 iframe 显示。

`integrations/clipforge` 只作为**源码**。构建产物 `.next/standalone` 是可被 `next build` 清理的临时目录，**不能**直接运行或打包：MainPG 只运行经过校验、发布到独立目录里的不可变 artifact，入口恒为 `<app-root>/server.js`。发布流程会做静态校验（`BUILD_ID`、`required-server-files.json` 列出的全部文件、middleware manifest、Next 与 traced dependencies 必须从 artifact 内部解析、媒体二进制的平台/架构映射、Node ABI）和隔离 smoke test（临时数据目录启动、结构化健康、非空 `/start` HTML、运行前后产物哈希一致）。任何一项失败都不会更新 `current.json`，残缺产物既不会被启动也不会进入安装包。

发布 Windows 安装包时，`local-runtime/build_installer.ps1` 会自动安装锁定依赖、构建 ClipForge、发布并校验 artifact，然后把已验证的部署根与 Node 运行时一起放入安装包。构建机需要 Node 20+ 和 pnpm 10+；安装后的用户不需要另装 Node。

### AI 视频 sidecar：开发态构建与验收

```bash
cd integrations/clipforge
pnpm install --frozen-lockfile
pnpm build
pnpm prepare:mainpg -- --output-root ../../local-runtime/outputs/wh-local/clipforge

cd ../../local-runtime
CLIPFORGE_APP_ROOT="$(python -c 'import json,pathlib; p=pathlib.Path("outputs/wh-local/clipforge"); print(p / json.loads((p / "current.json").read_text())["relativePath"])')"
python devtools/verify_clipforge_sidecar.py --app-root "$CLIPFORGE_APP_ROOT"
```

当前部署根由 `local-runtime/outputs/wh-local/clipforge/current.json` 指向：MainPG 后端与验收脚本都从这个指针解析 `<app-root>/server.js`。请勿直接运行 `integrations/clipforge/.next/standalone/server.js`——那条路径会被下一次 `next build` 清理，且缺少发布的媒体二进制与 manifest 校验。

验收脚本会启动 sidecar、校验 `/api/health` 与 `/start?embed=mainpg`、停止并重启（要求新的 `instanceId`）、确认没有残留 Node 进程，并比较运行前后的 artifact 全树哈希；任一项不满足都会以非零码退出。

媒体生成平台仅开放火山引擎（豆包/Seedance、Seedream）与速创。速创的后续模型可在 ClipForge 的“生成设置”中以模型 ID 添加。详见 [ClipForge AGPL 说明](docs/licenses/clipforge-AGPL.md)。
