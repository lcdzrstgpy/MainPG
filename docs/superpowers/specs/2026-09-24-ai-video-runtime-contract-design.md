# MainPG AI 视频运行链整理设计

> 状态：设计已确认，待编写实施计划  
> 日期：2026-09-24  
> 基线：`751909c3408e9b1b98b9755fa59cbae8859d3f6f`  
> 适用范围：MainPG 前端、MainPG 本地后端、内置 `integrations/clipforge`

## 1. 背景与问题定义

MainPG 的 AI 视频链路目前为：

```text
MainPG Web 5173
  -> MainPG FastAPI 8010
  -> ClipForge Next 随机回环端口
  -> MainPG iframe
```

这条链路的职责划分本身可以保留。当前故障来自构建、部署、启动和健康检查之间没有形成完整契约：

1. MainPG 直接运行 ClipForge 的可变构建目录 `.next/standalone`。后续 `next build` 会清理该目录，能够撕裂正在使用的运行树。
2. 现有构建验收只检查 `server.js` 或少量依赖，不能识别缺少 `BUILD_ID`、middleware manifest、traced dependencies 等残缺产物。
3. 开发环境允许 Node 从源码根的 `node_modules` 回退解析依赖，使损坏的 standalone 看起来仍能启动；安装包中没有这种回退路径。
4. 安装器将入口复制为 `clipforge/app/server.js`，服务层却继续拼接 `.next/standalone/server.js`，开发态和安装态采用了不同且互相矛盾的目录假设。
5. MainPG 只要收到任意 `<500` HTTP 响应就判定 ready。空页面、400、404 甚至业务依赖失败均可能显示“已连接”。
6. 后端没有稳定的 `starting`、`stopping` 和持久 `failed` 状态；前端也没有持续轮询，因此 UI 会长期保留过期状态。
7. Electron 和 MainPG Node sidecar 共用同一 standalone 后处理目录，但 `better-sqlite3` 的 ABI 目标不同，存在互相污染风险。

因此本设计不重写三方调用链，也不迁移 ClipForge 的业务 API；重点是建立唯一、可验证且不可变的运行契约。

## 2. 目标与非目标

### 2.1 目标

1. `integrations/clipforge` 是 MainPG 使用的唯一 ClipForge 源码。
2. 构建目录与运行产物分离，运行期间不修改部署产物。
3. 开发态和安装态都以“部署根目录中的 `server.js`”作为唯一入口契约。
4. MainPG 后端提供可观察、可恢复且不会产生假 ready 的生命周期状态。
5. MainPG 前端展示真实服务状态，并把后端健康与 iframe bridge 健康分开。
6. 用自动化测试覆盖残缺构建、路径错误、进程退出、重启和前端过期状态。

### 2.2 非目标

- 不迁移 ClipForge 的 73 个业务 API 到 FastAPI。
- 不同步或运行 `/Users/Zhuanz/Desktop/电商视频/clipforge`；该仓库只作上游参考。
- 不修改九宫格、速创、商品处理或 POD 业务逻辑。
- 不重新设计 AI 视频页面视觉。
- 不改变现有 ClipForge 项目数据和业务路由。
- 不恢复或混入原主工作区的 POD 改动、无关删除或已消失的未提交修改。

## 3. 选定方案

### 3.1 方案 A：不可变 sidecar 产物 + 明确运行契约（采用）

构建工具从 `integrations/clipforge` 生成 standalone，经完整验证后发布到独立的版本化产物目录。MainPG 服务只运行已发布产物，不读取正在生成的 `.next`。

优点：从根本上隔离构建和运行，开发态与安装态使用相同布局；能够可靠复现和诊断。代价：需要新增发布/验证步骤，并改造服务的入口解析。

### 3.2 方案 B：继续运行 `.next/standalone`，启动前补文件

改动较少，但任何并发构建仍会删除运行文件；复制 manifests 不能补齐 traced dependencies，也无法解决 ABI 和安装路径问题。不采用。

### 3.3 方案 C：将 ClipForge API 全部迁移到 MainPG 后端

最终可减少 sidecar，但迁移面包含大量 Next API、数据库和媒体流水线，回归风险远超本次运行链整理目标。不采用。

## 4. 目标架构与目录契约

### 4.1 源码、构建、部署、数据四层分离

```text
integrations/clipforge/                 # 唯一源码
  .next/                               # 临时构建目录，可被 Next 清理

local-runtime/outputs/wh-local/clipforge/
  artifacts/<artifact-id>/             # 开发态不可变部署根
    server.js
    package.json
    node_modules/
    .next/
    public/
    drizzle/
  current.json                         # 原子更新的当前产物指针
  data/                                # 运行数据
  logs/                                # 按启动尝试分隔的日志

<packaged install root>/clipforge/app/ # 安装态部署根
  server.js
  package.json
  node_modules/
  .next/
  public/
  drizzle/
```

开发态发布采用版本化目录，运行中的旧实例可以继续读取旧产物。验证全部通过后，只原子替换 `current.json`；新实例在启动时解析该指针。安装器复制已经验证的某个部署根到固定的 `clipforge/app`，不再重新拼装内容。

运行数据和日志始终位于可写数据目录，禁止写入产物树。首阶段不自动清理历史产物，避免误删仍被运行实例引用的目录；产物保留策略另行处理。

### 4.2 唯一入口接口

`ClipForgeService` 的构造参数从模糊的 `source_root` 改为明确的 `app_root`。入口恒为：

```text
<app_root>/server.js
```

开发态由 resolver 从 `current.json` 解析 `app_root`；安装态为 `<install_root>/clipforge/app`。服务层不得再追加 `.next/standalone`，也不得复制 static、修改 chmod 或修补 manifests。

### 4.3 Node 与 Electron 产物隔离

MainPG sidecar 发布流程只生成 Node ABI 产物。Electron 的 `better-sqlite3` 重建继续使用独立输出，不能原地修改 MainPG sidecar。验证器记录并检查产物目标，拒绝把 Electron 标记或不匹配的 native module 当作 MainPG 产物发布。

## 5. 构建、发布与验证契约

新增一个 MainPG sidecar 发布入口，职责固定为：

1. 从干净的 `.next` 执行一次 `pnpm build`。
2. 将 standalone、`.next/static`、`public`、`drizzle` 和 MainPG Node 运行所需媒体依赖复制到临时 staging。
3. 在 staging 内完成静态验证。
4. 以 staging 中的内容启动一次隔离 smoke test。
5. 计算不可变 `artifact-id`，发布到版本化目录。
6. 原子更新 `current.json`。

静态验证必须检查：

- `server.js`、`package.json`、`.next/BUILD_ID`；
- `.next/required-server-files.json` 中列出的每个文件；
- `.next/server/middleware-manifest.json`、路由和 app manifests；
- `node_modules/next/package.json` 及 traced dependencies；
- `public`、`.next/static`、`drizzle`；
- FFmpeg 和 FFprobe 的目标平台/架构映射；
- `next`、`@swc/helpers`、`styled-jsx` 等依赖必须从 staging 内解析，禁止回退源码根；
- native module 为 MainPG Node ABI，而非 Electron ABI。

smoke test 使用隔离的临时 `APP_DATA_DIR` 启动 staging，验证 `/api/health` 和 `/start?embed=mainpg`。页面响应必须为非空 HTML。smoke test 前后比较产物树哈希，确保运行期零修改。

任何检查失败都保留旧 `current.json`，残缺 staging 不得成为当前产物，也不得进入安装包。

## 6. MainPG 后端生命周期设计

### 6.1 状态模型

```python
ClipForgeRuntimeState = Literal[
    "unavailable", "stopped", "starting", "ready", "failed", "stopping"
]
ClipForgeBuildState = Literal["available", "missing", "invalid"]
```

对外状态结构为：

```json
{
  "state": "ready",
  "buildState": "available",
  "available": true,
  "url": "http://127.0.0.1:58922",
  "instanceId": "unique-runtime-id",
  "message": "AI 视频服务已就绪",
  "error": null
}
```

`available` 为兼容字段，严格等价于 `buildState == "available"`，不表示进程已经 ready。

错误结构为：

```json
{
  "code": "CLIPFORGE_HEALTHCHECK_FAILED",
  "message": "AI 视频服务启动后未通过健康检查",
  "retryable": true,
  "exitCode": 1,
  "diagnosticId": "short-id"
}
```

原始异常、堆栈和本机路径只写日志，不进入浏览器响应。

### 6.2 启动行为

- `start()` 只做同步校验和 `stopped/failed -> starting` 状态转换，然后启动后台 worker 并立即返回。
- 对 `starting` 或 `ready` 再次调用是幂等操作，不创建第二个进程。
- 每次启动生成新的 `instanceId` 和 generation。过期 worker、旧健康请求或旧进程 watcher 不得覆盖当前实例状态。
- worker 启动进程后先验证结构化健康，再把状态改为 `ready`。
- 进程 watcher 发现退出时，将当前 generation 转为 `failed`，保存退出码和诊断编号。
- `failed` 持续保留，直到明确发起下一次启动或成功恢复，不能被一次 `status()` 查询重置为 `stopped`。
- 锁只保护短状态变更，不覆盖进程启动和最长 30 秒的健康等待。

### 6.3 停止与应用退出

- `stop()` 执行 `ready/starting/failed -> stopping -> stopped`。
- macOS/Linux 使用独立 process group；Windows 使用独立 process group/job-compatible tree termination。
- 先发送温和终止，限时等待后终止整棵进程树，避免遗留 Node 或 FFmpeg。
- FastAPI lifespan、桌面退出 watchdog 和显式停止复用同一清理路径。
- 现有 `os._exit(0)` 之前必须先完成有界的 ClipForge 清理；硬退出不能绕过 sidecar 回收。

## 7. ClipForge 健康契约

新增或收紧 `GET /api/health`，返回：

```json
{
  "service": "clipforge",
  "schemaVersion": 1,
  "instanceId": "unique-runtime-id",
  "status": "ok",
  "checks": {
    "database": { "status": "ok" },
    "migrations": { "status": "ok" },
    "dataDirWritable": { "status": "ok" },
    "ffmpeg": { "status": "ok" },
    "ffprobe": { "status": "ok" }
  }
}
```

MainPG 只有在以下条件全部满足时才进入 `ready`：

1. 子进程仍存活；
2. `/api/health` 返回 HTTP 200；
3. JSON schema 和版本有效；
4. `service == "clipforge"`；
5. `instanceId` 与本次启动一致；
6. 顶层 `status == "ok"`；
7. 所有关键 checks 均为 `ok`；
8. `/start?embed=mainpg` 返回非空 HTML。

400、404、空 200、无效 JSON 或任一依赖失败都不得作为 ready。健康接口不返回绝对路径、密钥或供应商响应正文。

## 8. MainPG HTTP 接口

保留现有路径，收紧语义：

| 接口 | 行为 |
|---|---|
| `GET /api/clipforge/status` | 始终返回 200 和当前状态快照 |
| `POST /api/clipforge/start` | 已 ready 返回 200；已接受启动或正在启动返回 202 |
| `POST /api/clipforge/start` | 构建缺失/无效返回 409 和结构化错误 |
| `POST /api/clipforge/start` | 非预期内部错误返回 500 和结构化错误 |

状态快照是前端判断依据。HTTP 200 不再表示 ClipForge 一定 ready，调用方必须检查 `state`。

## 9. MainPG 前端设计

### 9.1 状态展示

| 后端状态 | 前端行为 |
|---|---|
| `unavailable` | 显示产物缺失或损坏，禁用无意义的重复启动 |
| `stopped` | 显示未启动和启动按钮 |
| `starting` | 显示启动中，禁用按钮，约 800ms 轮询 |
| `ready` | 挂载 iframe，约 5 秒轮询后端状态 |
| `failed` | 显示稳定错误、诊断编号和重新启动按钮 |
| `stopping` | 显示正在关闭，不挂载 iframe |

轮询需要 request sequence 或 `AbortController`，防止较慢的旧请求覆盖新状态。组件卸载时停止轮询。

### 9.2 两层就绪

前端分别维护：

1. `serviceReady`：后端报告当前实例健康；
2. `bridgeReady`：当前 iframe 完成既有 MainPG bridge 握手。

只有两者同时为真才显示“AI 视频已连接”。`instanceId` 或 URL 改变时必须销毁旧 iframe、清空导航和 bridge 状态，再创建新实例。bridge 在 iframe load 后 10 秒仍未完成握手时显示嵌入连接错误，但不把后端服务误报为 failed。

本阶段保留现有七个二级导航入口和消息协议，不调整 ClipForge 内部业务 UI。

### 9.3 组件边界

- `clipforgeApi.ts`：只定义 HTTP DTO 和调用。
- `useClipForgeService`：负责轮询、启动和陈旧响应保护。
- `AiVideoServiceState`：负责 unavailable/stopped/starting/failed/stopping 的可见状态。
- `AiVideoPage`：只负责 ready 后的导航、iframe 和 bridge 生命周期。

若现有目录约定不适合新增 hook，可采用等价命名，但不得把轮询、桥接和错误展示继续堆在一个 effect 中。

## 10. 日志与诊断

- 每次启动生成 `diagnosticId`，对应单独或可明确分段的日志记录。
- 日志包含 artifact id、instance id、generation、端口、状态转换和退出码。
- 日志大小有上限或轮转，不能继续无限追加单个文件。
- 前端只显示稳定错误码、用户文案和诊断编号。
- 首阶段不新增远程诊断上传接口，也不把完整日志暴露为 unauthenticated HTTP endpoint。

## 11. 测试设计

### 11.1 构建契约测试

- 只有 `server.js`、缺少 `BUILD_ID` 时发布失败。
- 缺任一 required-server-file 或 middleware manifest 时发布失败。
- standalone 自身缺依赖、但源码根存在完整 `node_modules` 时，验证器识别越界解析并失败。
- Electron ABI 产物作为 MainPG sidecar 输入时失败。
- 验证失败不更新 `current.json`。
- smoke test 前后产物树哈希一致。

### 11.2 后端单元与路由测试

- app root 在开发态和安装态都解析到 `<root>/server.js`。
- 状态转换覆盖 unavailable、stopped、starting、ready、failed、stopping。
- 重复启动幂等；旧 generation 不覆盖新实例。
- 400、404、空 200、错误 JSON、instance mismatch 和依赖失败均不 ready。
- 进程意外退出后 failed 持久保留。
- stop 和应用退出终止完整进程树。
- 路由验证 200、202、409、500 的状态与响应体。

### 11.3 前端测试

- 六种后端状态对应正确 UI 和按钮行为。
- starting/ready 使用不同轮询间隔并在卸载时停止。
- `instanceId` 或 URL 变化会重置 iframe、导航和 bridge。
- 后端 ready 但 bridge 超时时，不显示“已连接”。
- 旧请求不能覆盖较新的状态。
- 新增行为测试不得只依赖读取源码后匹配正则。

### 11.4 端到端验收

1. 从干净源码构建并发布 MainPG sidecar。
2. 启动 MainPG 后端，确认 `POST /start` 快速返回 202。
3. 轮询至 ready，验证结构化健康和非空 `/start` HTML。
4. 打开 MainPG AI 视频页，确认 iframe bridge 完成。
5. 杀死 sidecar，确认后端和前端进入 failed。
6. 点击重启，确认新 `instanceId`、新 iframe 和正常恢复。
7. 停止 MainPG，确认不存在遗留 Node/FFmpeg 子进程。
8. 比较运行前后 artifact hash，确认产物未被修改。

## 12. 实施顺序与提交边界

按以下独立提交推进：

1. `build(ai-video): define and validate immutable sidecar artifact`
2. `refactor(ai-video): resolve one ClipForge app-root contract`
3. `refactor(ai-video): model asynchronous sidecar lifecycle`
4. `fix(ai-video): require structured ClipForge health`
5. `fix(ai-video): render and poll truthful service state`
6. `test(ai-video): cover end-to-end sidecar recovery`

每一步先补失败测试，再做最小实现。任何步骤不得顺带提交主工作区原有 POD 改动、发布脚本删除或资源删除。

## 13. 完成标准

- MainPG 不再直接运行 `integrations/clipforge/.next/standalone`。
- 开发态和安装态入口都严格为 `<app_root>/server.js`。
- 缺 `BUILD_ID`、manifests 或 runtime dependencies 的产物无法发布、打包或启动。
- ClipForge 运行期间产物树哈希不变。
- 后端不会把 400、404、空 200 或依赖失败判为 ready。
- 前端不会在服务退出或实例变化后保留旧的“已连接”状态。
- MainPG 退出后无遗留 ClipForge Node/FFmpeg 进程。
- 构建契约、后端、前端和端到端测试全部通过。

