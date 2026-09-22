# MainPG
A new environment

## AI 视频模块

ClipForge 的完整源码已直接纳入 `integrations/clipforge`；它不是 Git submodule，也不在运行时引用外部仓库。MainPG 启动时在本机回环地址管理它的 Next standalone 服务，并在工作台的“AI 视频”入口中显示该界面。

发布 Windows 安装包时，`local-runtime/build_installer.ps1` 会自动安装锁定依赖、构建 ClipForge standalone，并连同 Node 运行时和媒体二进制放入安装包。构建机需要 Node 20+ 和 pnpm 10+；安装后的用户不需要另装 Node。

开发态首次构建：

```powershell
cd integrations/clipforge
pnpm install --frozen-lockfile
pnpm build
node scripts/prepare-mainpg-sidecar.mjs
```

媒体生成平台仅开放火山引擎（豆包/Seedance、Seedream）与速创。速创的后续模型可在 ClipForge 的“生成设置”中以模型 ID 添加。详见 [ClipForge AGPL 说明](docs/licenses/clipforge-AGPL.md)。
