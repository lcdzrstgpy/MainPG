# 无音 GPT-Image-2.5 接入与「后台管理模型选择」技术指导文档

> 版本：v1 · 适用代码：MainPG 1.4.2 服务端（`/opt/wh-workbench/MainPG/local-runtime`）+ wh-admin（`/opt/wh-admin`）
> 目标：接入无音新接口 `POST /api/async/image_gpt_2.5`，并在后台（wh-admin）管理生图模型选择，支持一键切换 / 回滚。

---

## 0. 结论摘要（先看这一节）

1. **只需要改服务端，桌面端不用发版。** 生产环境的生图走「服务端托管网关」：桌面端只 POST `{usage_id, prompt, size, urls}` 到 `/api/customer/ai/image`，**不传 model**。真正决定用哪个无音模型的是服务端 [auth_server.py](file:///e:/MainPG/MainPG/local-runtime/wh_local/customer/auth_server.py) 里的硬编码 URL。所以「后台管理模型选择」= 改服务端 + wh-admin。
2. **新接口和旧接口的字段不一样**：旧接口用 `size`（比例串，如 `1:1`），新接口用 `aspectRatio`（像素串，如 `1024x1024`）。必须做一层字段适配。（早期版本还提到 `quality`，该字段已被上游移除，**不要传**。）
3. **推荐落点：把模型挂在「图片密钥」上。** 图片密钥库里同一时刻只有一把 `active`（见 [credential_vault.py](file:///e:/MainPG/MainPG/local-runtime/wh_local/customer/credential_vault.py#L126-L134)），把 `model` 作为该密钥的属性存起来，wh-admin 加一个下拉框即可。切换/回滚 = 改一个字段，**无需重启、无需发版**。
4. **三个必须注意的坑**：
   - 幂等指纹 `_gateway_request_hash({prompt,size,urls})` 必须把 `model` 纳进去，否则换模型后会命中旧模型的缓存结果（[auth_server.py](file:///e:/MainPG/MainPG/local-runtime/wh_local/customer/auth_server.py#L1519-L1521)）。
   - 新接口 `aspectRatio` 只有 **1K 档**；客户端默认 2K（`2048x2048`）经 `_wuyin_size()` 会压成 `1:1`，映射到 2.5 就是 **1024×1024**，分辨率下降。需要业务上确认是否可接受。
   - 桌面端有一条「直连模式」（批次下发短期密钥时绕过网关），它写死了 `/api/async/image_gpt`，**后台切 2.5 对直连模式不生效**。生产若全量走托管则可忽略，否则见 §4.4。

---

## 1. 现状梳理

### 1.1 生产调用链（服务端托管，唯一需要改的链路）

```
桌面端 ProductImageProcessor
  └─ media._request_server_managed_wuyin_image()
       POST {gateway}/api/customer/ai/image
       body = {usage_id, prompt, size(比例串), urls?}     ← 不含 model
          │
服务端 auth_server.server_managed_ai_image()               ← 唯一的模型决策点
  ├─ 校验 usage / feature / size 白名单
  ├─ request_hash = _gateway_request_hash({prompt,size,urls})
  ├─ api_key = _server_provider_secret("image", "WH_WUYIN_IMAGE_API_KEY")
  ├─ _submit_server_wuyin(api_key, prompt, urls, size)      ← URL 硬编码决定模型
  │     POST https://api.wuyinkeji.com/api/async/image_gpt  ← 模型在这里
  ├─ _poll_server_wuyin(api_key, task_id)                   ← /api/async/detail
  └─ 返回 {ok, task_id, result_url}
          │
结算：_fixed_usage_provider(feature_key) → ("wuyin", "image_gpt")   ← 模型再次硬编码
```

### 1.2 模型硬编码点清单（改造前）

| 位置 | 内容 | 影响 |
| --- | --- | --- |
| [auth_server.py#L112-L113](file:///e:/MainPG/MainPG/local-runtime/wh_local/customer/auth_server.py#L112-L113) | `WUYIN_IMAGE_SUBMIT_URL = ".../image_gpt"` | **上游走哪个模型，唯一事实源** |
| [auth_server.py#L2002-L2007](file:///e:/MainPG/MainPG/local-runtime/wh_local/customer/auth_server.py#L2002-L2007) | `_fixed_usage_provider` 写死 `("wuyin","image_gpt")` | 计费/账单里的模型名 |
| [auth_server.py#L2631-L2646](file:///e:/MainPG/MainPG/local-runtime/wh_local/customer/auth_server.py#L2631-L2646) | `_submit_server_wuyin` 请求体固定 `{prompt,size,urls}` | 字段结构 |
| [credential_vault.py#L215-L217](file:///e:/MainPG/MainPG/local-runtime/wh_local/customer/credential_vault.py#L210-L224) | `add_credential` 写死 `"model": "image_gpt"` | 密钥元数据里的模型名 |
| [billing.py#L684-L685](file:///e:/MainPG/MainPG/local-runtime/wh_local/billing.py#L684-L685) | 兜底结算写死 `model="image_gpt"` | 极端路径的账单模型名（次要） |
| [media.py#L87](file:///e:/MainPG/MainPG/local-runtime/wh_local/modules/product_processing/infrastructure/media.py#L87) | `WUYIN_IMAGE_SUBMIT_PATH = "/api/async/image_gpt"` | 仅直连模式使用 |
| [provider_config.py#L30-L40](file:///e:/MainPG/MainPG/local-runtime/wh_local/modules/product_processing/provider_config.py#L30-L40) | `IMAGE_MODEL` / `IMAGE_MODEL_POOL` = `image_gpt` | 托管模式下**不生效**；仅直连/OpenAI 兼容分支使用 |

> 结论：托管模式下，桌面端的 `IMAGE_MODEL` / `IMAGE_MODEL_POOL` / `image_model` 都是「摆设」。改服务端即可覆盖全部生产流量。

### 1.3 新旧接口差异

| 维度 | 旧接口 `image_gpt` | 新接口 `image_gpt_2.5` |
| --- | --- | --- |
| 路径 | `/api/async/image_gpt` | `/api/async/image_gpt_2.5` |
| 尺寸字段 | `size`，**比例串**（`1:1`/`16:9`/`3:4`…含 `auto`） | `aspectRatio`，**像素串**（`1024x1024`/`1280x720`/`864x1152`…） |
| 质量字段 | 无 | 无。文档早期版本提到的 `quality` 已被上游移除，**传了会被以 `code 500 转发请求失败: 存在未绑定的参数: quality` 拒绝** |
| 认证 | query `key` + header `Authorization`（两者都传，现有实现一致） | 同左（**无需改动**） |
| 提交返回 | `{code:200, data:{id, count}}` | 同左（**无需改动**） |
| 轮询 | `/api/async/detail` | 同左（**无需改动**） |
| 参考价 | — | 0.1 元 / 张，100 QPS，无免费额度 |

`aspectRatio` 全量映射（1K 档）：

| 比例 | 像素 | 比例 | 像素 | 比例 | 像素 |
| --- | --- | --- | --- | --- | --- |
| 1:1 | 1024×1024 | 3:2 | 1536×1024 | 1:3 | 688×2048 |
| 16:9 | 1280×720 | 2:3 | 1024×1536 | 3:1 | 2048×688 |
| 9:16 | 720×1280 | 5:4 | 1120×896 | 2:1 | 1536×768 |
| 4:3 | 1152×864 | 4:5 | 896×1120 | 1:2 | 768×1536 |
| 3:4 | 864×1152 | 21:9 | 1456×624 | 9:21 | 624×1456 |

> 注意：新接口**不含 2K/4K 档**（文档给的是 1K）。在线调试页提示「如 2448×3264、1536×1024 等」，若实际支持更大像素，可后续把映射表扩成「比例 → 像素」可配置，但**不要**在未验证前默认发大图。

---

## 2. 方案设计

### 2.1 落点选择

| 方案 | 做法 | 影响面 | 是否推荐 |
| --- | --- | --- | --- |
| **A. 模型挂在图片密钥上 + wh-admin 下拉** | 密钥库记录新增 `model` 字段；网关读取当前 active 密钥的 model 决定 URL 与请求体 | 全平台，改 3 个文件 + wh-admin | ✅ **推荐** |
| B. 全局环境变量 `WH_WUYIN_IMAGE_MODEL` | systemd 环境变量控制，无 UI | 全平台，但改配置要重启、无界面 | 作为 A 的**兜底/kill-switch**保留 |
| C. 桌面端系统配置 `image.model` | 走 `basic_settings` → `resolve_ai_provider()` | **托管模式下不生效**（桌面端不传 model），且需发版 | ❌ 不采用 |
| D. ai_service 模型目录 | 复用 `ai_service` 的 bootstrap/落库 | 那是**文本对话模块**的目录，与生图网关无关 | ❌ 不采用 |

选 **A**，理由：
- 图片密钥库天然「同时只有一把 active」，模型是密钥的天然属性；
- wh-admin 已有完整的密钥增删改启 UI，增量最小；
- 切换 = 改字段 / 或激活另一把密钥，**零重启、零发版**；
- 回滚 = 把 model 改回 `image_gpt`，秒级生效。

### 2.2 读取优先级（改造后）

```
网关取「图片上游凭据 + 模型」时：
  1) 密钥库 active image credential 的 model   ← 后台可管理（主路径）
  2) 环境变量 WH_WUYIN_IMAGE_MODEL              ← 应急 kill-switch
  3) 常量默认 image_gpt                         ← 最终兜底
其中 (1) 的 secret 仍沿用现有 active_secret / legacy env 回退逻辑，不改变安全模型。
```

---

## 3. 代码级改造清单

> 所有改动都在服务端（`local-runtime`）与 wh-admin（`_whadmin_live`）。桌面端**不新增任何代码**。

### 3.1 `wh_local/customer/credential_vault.py`

**改动 1：新增模型白名单与规范化函数**（放在 `_KINDS` 附近，L24 之后）

```python
_KINDS = {"text", "image"}
# 生图模型白名单：必须与 auth_server 的上游端点表一一对应。
# 新增模型时这里和 auth_server.WUYIN_IMAGE_MODEL_ENDPOINTS 要同时加。
IMAGE_MODELS = ("image_gpt", "image_gpt_2.5")
_TEXT_MODEL = "managed-text"
_DEFAULT_IMAGE_MODEL = "image_gpt"


def _normalize_model(kind: str, model: Any) -> str:
    """写入前的模型规范化：文本固定，图片必须命中白名单，否则报错。"""
    if kind == "text":
        return _TEXT_MODEL
    clean = str(model or "").strip()
    if not clean:
        return _DEFAULT_IMAGE_MODEL
    if clean not in IMAGE_MODELS:
        raise CredentialVaultError("image model is invalid")
    return clean
```

**改动 2：`add_credential` 增加 `model` 入参**（L189-225）

```python
def add_credential(
    kind: str,
    label: str,
    secret: str,
    *,
    activate: bool = True,
    max_concurrency: int | None = None,
    model: str | None = None,          # ← 新增
) -> dict[str, Any]:
    clean_kind = _validate_kind(kind)
    ...
    record = {
        "credential_id": f"cred_{secrets.token_urlsafe(18)}",
        "kind": clean_kind,
        "label": clean_label,
        "provider": "platform_text" if clean_kind == "text" else "wuyin",
        "model": _normalize_model(clean_kind, model),   # ← 替换写死的 image_gpt
        ...
    }
```

**改动 3：`update_credential` 支持改模型**（L228-253）

```python
def update_credential(
    credential_id: str,
    *,
    label: str | None = None,
    secret: str | None = None,
    max_concurrency: int | None = None,
    model: str | None = None,          # ← 新增
) -> dict[str, Any]:
    ...
        if max_concurrency is not None:
            record["max_concurrency"] = _max_concurrency(...)
        if model is not None:
            record["model"] = _normalize_model(
                _validate_kind(str(record.get("kind") or "")), model
            )
        record["updated_at"] = _timestamp()
```

**改动 4：新增 `active_credential()`，一次读取「密钥 + 模型」，避免读到两次不同快照**

```python
def active_credential(kind: str) -> dict[str, Any] | None:
    """返回当前启用密钥的元数据 + 解密后的密钥。

    网关需要同时拿到 secret 和 model；放在一次加锁读取里，
    避免两次调用之间管理员切换密钥导致「旧密钥 + 新模型」错配。
    """
    clean_kind = _validate_kind(kind)
    with _LOCK:
        document = _load()
        record = next(
            (
                item
                for item in document["credentials"]
                if isinstance(item, dict)
                and item.get("kind") == clean_kind
                and item.get("enabled")
                and item.get("active")
            ),
            None,
        )
        if record is None:
            return None
        try:
            secret = _fernet().decrypt(
                str(record.get("ciphertext") or "").encode("ascii")
            ).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise CredentialVaultError("active credential cannot be decrypted") from exc
        return {
            "credential_id": str(record.get("credential_id") or ""),
            "kind": clean_kind,
            "model": _normalize_model(clean_kind, record.get("model")),
            "secret": secret,
            "max_concurrency": _max_concurrency(clean_kind, record.get("max_concurrency")),
        }
```

（可选）把 `active_secret()` 改为 `return (active_credential(kind) or {}).get("secret")`，减少重复代码；不改也不影响。

**向后兼容**：历史密钥记录里已有 `"model": "image_gpt"`，命中白名单，无需迁移。

---

### 3.2 `wh_local/customer/auth_server.py`

**改动 1：把单个 URL 常量替换为「模型 → 端点」表**（L112-113）

```python
# 上游：模型由 URL 路径决定，请求体不含 model 字段。
WUYIN_IMAGE_MODEL_ENDPOINTS = {
    "image_gpt": "https://api.wuyinkeji.com/api/async/image_gpt",
    "image_gpt_2.5": "https://api.wuyinkeji.com/api/async/image_gpt_2.5",
}
WUYIN_IMAGE_DEFAULT_MODEL = "image_gpt"
WUYIN_IMAGE_DETAIL_URL = "https://api.wuyinkeji.com/api/async/detail"
# 2.5 专用：比例串 → 1K 像素串（仅 1K 档，见文档 §1.3）
WUYIN_ASPECT_RATIO_2_5 = {
    "1:1": "1024x1024", "16:9": "1280x720", "9:16": "720x1280",
    "4:3": "1152x864", "3:4": "864x1152", "3:2": "1536x1024",
    "2:3": "1024x1536", "5:4": "1120x896", "4:5": "896x1120",
    "21:9": "1456x624", "9:21": "624x1456", "1:3": "688x2048",
    "3:1": "2048x688", "2:1": "1536x768", "1:2": "768x1536",
}
```

> 注意：删除或保留 `WUYIN_IMAGE_SUBMIT_URL` 均可。若保留，令其为 `WUYIN_IMAGE_MODEL_ENDPOINTS["image_gpt"]` 以兼容其它引用（当前仅 L2640 使用，会被改动 3 替换）。

**改动 2：新增「取密钥 + 模型」的解析函数**（建议放在 `_server_provider_secret` 附近，L442 之后）

```python
def _gateway_image_model() -> str:
    """当前生效的生图模型：密钥库 > 环境变量 > 默认。"""
    model = ""
    try:
        record = credential_vault.active_credential("image")
        model = str((record or {}).get("model") or "").strip()
    except CredentialVaultError:
        model = ""
    if model not in WUYIN_IMAGE_MODEL_ENDPOINTS:
        env_model = (os.environ.get("WH_WUYIN_IMAGE_MODEL") or "").strip()
        model = env_model if env_model in WUYIN_IMAGE_MODEL_ENDPOINTS else WUYIN_IMAGE_DEFAULT_MODEL
    return model


def _gateway_image_provider() -> tuple[str, str]:
    """返回 (api_key, model)。保留 legacy 环境变量回退语义，不改变安全模型。"""
    legacy = str(os.environ.get("WH_WUYIN_IMAGE_API_KEY") or "").strip()
    try:
        record = credential_vault.active_credential("image")
    except CredentialVaultError as exc:
        if legacy:
            return legacy, _gateway_image_model()
        raise HTTPException(
            status_code=503,
            detail="server image credential vault is unavailable",
        ) from exc
    secret = str((record or {}).get("secret") or "").strip()
    if not secret:
        return legacy, _gateway_image_model()
    model = str((record or {}).get("model") or "").strip()
    if model not in WUYIN_IMAGE_MODEL_ENDPOINTS:
        model = _gateway_image_model()
    return secret, model
```

> 导入说明：文件顶部已可访问 `credential_vault`（`_server_provider_secret` 用的是 `active_secret`/`CredentialVaultError`）。若当前是 `from ... import active_secret`，需相应补 `active_credential` 的导入。

**改动 3：`_fixed_usage_provider` 图片分支返回真实模型**（L2002-2007）

```python
def _fixed_usage_provider(feature_key: str) -> tuple[str, str]:
    return (
        ("wuyin", _gateway_image_model())
        if feature_key in GATEWAY_IMAGE_FEATURE_KEYS
        else ("platform_text", "managed-text")
    )
```

**改动 4：路由内取 provider 并纳入幂等指纹**（L1508-1522、L1540）

```python
        size = str(payload.get("size") or "1:1").strip().lower()
        ...（size 白名单校验不变）...
        api_key, image_model = _gateway_image_provider()
        if not api_key:
            raise HTTPException(status_code=503, detail="server image credential is not configured")
        # model 必须参与指纹：否则切换模型后会命中上一模型的缓存结果。
        request_hash = _gateway_request_hash(
            {"prompt": prompt, "size": size, "urls": urls, "model": image_model}
        )
        claim = _claim_gateway_request(...)
        ...
                task_id = _submit_server_wuyin(api_key, prompt, urls, size, image_model)
```

> `_claim_gateway_request` 的调用位置需相应下移到 `_gateway_image_provider()` 之后（原先 `api_key` 在 hash 之后取，现在两者顺序对调即可）。这是本次改动**唯一需要调整语句顺序**的地方。

**改动 5：`_submit_server_wuyin` 按模型组装请求**（L2631-2674）

```python
def _submit_server_wuyin(
    api_key: str,
    prompt: str,
    urls: list[str],
    size: str,
    model: str,
) -> str:
    endpoint = WUYIN_IMAGE_MODEL_ENDPOINTS.get(model, WUYIN_IMAGE_MODEL_ENDPOINTS[WUYIN_IMAGE_DEFAULT_MODEL])
    if model == "image_gpt_2.5":
        # 2.5：比例串 → 1K 像素串；无 size 字段；不接受 quality 字段。
        body: dict[str, Any] = {
            "prompt": prompt,
            "aspectRatio": WUYIN_ASPECT_RATIO_2_5.get(size, "1024x1024"),
        }
    else:
        body = {"prompt": prompt, "size": size}
    if urls:
        body["urls"] = urls
    response: requests.Response | None = None
    try:
        response = requests.post(
            endpoint,
            params={"key": api_key},
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=35,
            allow_redirects=False,
            stream=True,
        )
        ...（以下 status/code/task_id 解析逻辑完全不变）...
```

**改动 6：`_server_image_request` 同步传参**（L2617-2628，当前为未引用的辅助函数，但需保持签名一致以免误用）

```python
def _server_image_request(api_key: str, prompt: str, urls: list[str], size: str) -> dict[str, Any]:
    _, model = _gateway_image_provider()
    task_id = _submit_server_wuyin(api_key, prompt, urls, size, model)
    ...
```

---

### 3.3 wh-admin `app.py`

来源：本地 [\_whadmin_live/app.py](file:///e:/MainPG/MainPG/local-runtime/_whadmin_live/app.py#L881-L927) → 远端 `/opt/wh-admin/app.py`

**改动 1：`POST /api/credentials` 透传 model**

```python
    item = vault.add_credential(
        kind,
        label,
        secret_value,
        activate=bool(payload.get("activate", True)),
        max_concurrency=payload.get("max_concurrency"),
        model=payload.get("model"),          # ← 新增
    )
```

**改动 2：`PATCH /api/credentials/{id}` 透传 model**

```python
    item = vault.update_credential(
        credential_id,
        label=str(payload["label"]) if "label" in payload else None,
        secret=str(secret_value) if secret_value is not None else None,
        max_concurrency=payload.get("max_concurrency"),
        model=payload.get("model") if "model" in payload else None,   # ← 新增
    )
```

> 校验失败会走 `CredentialVaultError` → 现有的 `400 密钥保存失败，请检查类型和密钥格式`，前端已有错误提示，无需新增分支。

---

### 3.4 wh-admin `index.html`

来源：本地 [\_whadmin_live/index.html](file:///e:/MainPG/MainPG/local-runtime/_whadmin_live/index.html#L247-L260) → 远端 `/opt/wh-admin/static/index.html`

**改动 1：新增表单里加「生图模型」下拉**（L249 的 `inline-form` 内）

```html
<label id="credential-model-wrap">生图模型
  <select id="credential-model" class="input">
    <option value="image_gpt">GPT-Image-2（现行）</option>
    <option value="image_gpt_2.5">GPT-Image-2.5（新版）</option>
  </select>
</label>
```

并把该行 `grid-template-columns` 增加一列（`150px minmax(160px,1fr) 145px minmax(260px,2fr) auto auto` → 追加一个 `170px`）。

**改动 2：联动显隐**（L501 `syncCredentialForm`）

```javascript
function syncCredentialForm(){
  const isText=$('credential-kind').value==='text';
  $('credential-concurrency-wrap').style.display=isText?'':'none';
  $('credential-max-concurrency').disabled=!isText;
  $('credential-model-wrap').style.display=isText?'none':'';   // ← 新增
}
```

**改动 3：创建请求带上 model**（L505 `credential-create-btn.onclick`）

```javascript
body: JSON.stringify({
  kind, label, secret, activate,
  max_concurrency: kind==='text'?maxConcurrency:1,
  model: kind==='image' ? $('credential-model').value : undefined,   // ← 新增
})
```

**改动 4：编辑流程支持改模型**（L506 `edit` 分支）

在 `edit` 分支里，`item.kind==='image'` 时增加一次选择（沿用现有 `prompt` 交互，最小改动）：

```javascript
let model=null;
if(item.kind==='image'){
  const raw=prompt('生图模型：image_gpt（GPT-Image-2）或 image_gpt_2.5（GPT-Image-2.5）', item.model||'image_gpt');
  if(raw===null)return;
  const clean=raw.trim();
  if(!['image_gpt','image_gpt_2.5'].includes(clean)){toast('模型只能是 image_gpt 或 image_gpt_2.5','error');return;}
  model=clean;
}
...
body: JSON.stringify({label,secret,max_concurrency:maxConcurrency,model})
```

**改动 5：文案**（L257 `form-note`）

把「图片密钥仍只使用当前启用的一把。」补一句：

> 图片密钥同一时间只使用当前启用的一把；新增/编辑时可选择该密钥对应的生图模型（GPT-Image-2 / GPT-Image-2.5），切换后即时生效、无需重启。

> 表格「服务 / 模型」列已渲染 `item.model`（L503），**无需改动**。

---

### 3.5 `wh_local/billing.py`（可选，建议做）

[auth_server.py `_fixed_usage_provider`](file:///e:/MainPG/MainPG/local-runtime/wh_local/customer/auth_server.py#L2002-L2007) 改造后，正常结算路径已记录真实模型。仅剩**兜底路径** [billing.py#L684-L685](file:///e:/MainPG/MainPG/local-runtime/wh_local/billing.py#L684-L685) 仍写死：

```python
provider = "wuyin" if str(row["feature_key"]) == "product_processing.image_grid_2k" else "aicoming"
model = "image_gpt" if provider == "wuyin" else "gpt-5.6-terra"
```

该路径是「业务失败但网关已成功」的兜底收费，发生频率低。若要求账单模型名 100% 准确，可在这里同样调用 `_gateway_image_model()`（需从 `customer.credential_vault` 导入）；否则可暂不改，**不影响功能**。

---

## 4. 关键风险与注意事项

### 4.1 幂等指纹必须包含 model（必做）
`_gateway_request_hash` 参与字段原为 `{prompt,size,urls}`。若不加入 `model`，第 1 次用旧模型生成后，管理员切到 2.5，**同样的 prompt 会直接命中旧的缓存**，用户永远看不到新模型效果。已在 §3.2 改动 4 处理。

### 4.2 分辨率会下降（业务需确认）
- 客户端默认 `IMAGE_SIZE = "2048x2048"`，`_wuyin_size()` 把它压成 `"1:1"`（[media.py#L1604-L1625](file:///e:/MainPG/MainPG/local-runtime/wh_local/modules/product_processing/infrastructure/media.py#L1604-L1625)）。
- 2.5 的 `1:1` = `1024x1024`。
- 即：**切到 2.5 后出图从 2048² 降到 1024²**。若业务不接受，需要在新接口上验证更大像素（如调试页提到的 2448×3264）后再开放，或仅对「参考图编辑 / 精品」等特定场景启用。

### 4.3 成本
- ~~`quality` 档位~~：上游已移除该参数，**不要再传**（传了 `code 500 存在未绑定的参数: quality`）。实现与文档均已按此调整。
- 2.5 参考价 **0.1 元/张**。现有计费按 `product_processing.image_grid_2k` 的积分规则（[billing.py#L62](file:///e:/MainPG/MainPG/local-runtime/wh_local/billing.py#L62)、[#L894-L907](file:///e:/MainPG/MainPG/local-runtime/wh_local/billing.py#L894-L907)）扣费。**若 0.1 元成本与现有积分定价不匹配，需要运营侧先核算毛利**，本方案不改定价。

### 4.4 直连模式不生效（已知边界）
批次冻结时服务端会下发短期密钥，客户端走 [media._request_wuyin_image()](file:///e:/MainPG/MainPG/local-runtime/wh_local/modules/product_processing/infrastructure/media.py#L1369-L1412) 直连 `api.wuyinkeji.com`，路径写死 `/api/async/image_gpt`（[media.py#L87](file:///e:/MainPG/MainPG/local-runtime/wh_local/modules/product_processing/infrastructure/media.py#L87)）。**后台切 2.5 对直连模式不生效。**
- 若生产 100% 走托管：忽略即可。
- 若要让直连模式也跟随：需要 (a) 下发密钥时同时下发 model，(b) 客户端按 model 选路径与字段，(c) **需要桌面端发版**。建议作为 Phase 2，与下次桌面端发版一起做。

### 4.5 超时预算
- 网关轮询预算 620s（[`_poll_server_wuyin`](file:///e:/MainPG/MainPG/local-runtime/wh_local/customer/auth_server.py#L2703-L2743)），usage 租约 900s（[L149-L152](file:///e:/MainPG/MainPG/local-runtime/wh_local/customer/auth_server.py#L149-L152)）。
- 2.5 若明显更慢，先观察 `exec_time` 与失败率；必要时再调 `GATEWAY_LEASE_SECONDS`，**本方案默认不动**。

### 4.6 安全模型不变
密钥仍为 Fernet 加密、仅服务端可解密；`model` 是元数据，随 `_public()` 以明文返回给管理员（与现有 `provider` 一致），不含敏感信息。

---

## 5. 实施与部署步骤

### 5.1 本地改动文件汇总
| 文件 | 改动 |
| --- | --- |
| `local-runtime/wh_local/customer/credential_vault.py` | 白名单 + `add/update` 支持 model + `active_credential()` |
| `local-runtime/wh_local/customer/auth_server.py` | 端点表 + 映射表 + 取模型 + 组装请求体 + 指纹含 model |
| `local-runtime/_whadmin_live/app.py` | 两个接口透传 `model` |
| `local-runtime/_whadmin_live/index.html` | 下拉 + 联动 + 请求体 + 编辑流程 + 文案 |
| `local-runtime/wh_local/billing.py` | （可选）兜底路径模型名 |

### 5.2 部署 auth-api（8011）
现有脚本 [\_deploy_auth_api.py](file:///e:/MainPG/MainPG/local-runtime/_deploy_auth_api.py) 已覆盖 `customer/auth_server.py`、`customer/credential_vault.py`、`billing.py`。执行后脚本会自动备份 + 上传 + `py_compile`；**重启 8011** 由脚本外的手动步骤完成：

```bash
systemctl restart auth-api     # 具体 unit 名以服务器实际为准（8011 由 Nginx /auth-api/ 反代）
systemctl is-active auth-api
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8011/health
```

### 5.3 部署 wh-admin（8012）
远端路径 `/opt/wh-admin/app.py`、`/opt/wh-admin/static/index.html`，venv `/opt/wh-admin/venv`：

```bash
cp -p /opt/wh-admin/app.py /opt/wh-admin/app.py.bak-$(date +%Y%m%d-%H%M%S)
# 上传 _whadmin_live/app.py 与 index.html
/opt/wh-admin/venv/bin/python -m py_compile /opt/wh-admin/app.py
systemctl restart wh-admin
systemctl is-active wh-admin
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8012/health
```

### 5.4 验证清单
1. **后台可管理**：wh-admin「密钥管理」能对图片密钥选 `GPT-Image-2.5` 并保存；列表「服务 / 模型」列显示 `wuyin / image_gpt_2.5`。
2. **切换即时生效**：把 active 图片密钥模型改为 `image_gpt_2.5`，做一单生图，观察服务端日志与上游返回的 `data.id` 前缀（2.5 返回形如 `image_xxx`）；**确认没有命中旧缓存**（用全新 prompt）。
3. **字段正确**：抓包/日志确认请求体是 `{prompt, aspectRatio, urls?}`，**不含 `size`、不含 `quality`**。
4. **回滚**：把模型改回 `image_gpt`，再跑一单，确认恢复到旧接口、旧分辨率。
5. **账单**：结算记录 `provider=wuyin`、`model=image_gpt_2.5`。
6. **应急开关**：`WH_WUYIN_IMAGE_MODEL=image_gpt` 时，即使密钥库选了 2.5 也应回到旧模型（验证优先级）。

### 5.5 灰度与回滚
- **灰度**：先只对「内测账号 / 单个图片密钥」改 model；生产密钥保持 `image_gpt`。因为模型挂在密钥上，可以先**新增一把同 key 的 `image_gpt_2.5` 密钥**做 A/B，验证 OK 再 `启用` 它（旧密钥自动变非 active，随时可切回）。
- **回滚**：把 active 密钥的 model 改回 `image_gpt`，或在列表点「启用」旧密钥。**无需重启、无需发版**。
- **紧急熔断**：`systemctl set-environment WH_WUYIN_IMAGE_MODEL=image_gpt`（或写入 unit 的 `Environment=`）后重启 auth-api。

---

## 6. 待确认事项（需业务/运营拍板）

1. **分辨率**：2.5 只给 1K，是否接受从 2048² 降到 1024²？是否要验证更大像素档？
2. **定价**：0.1 元/张 与现有积分定价是否需要对账调整？
3. **范围**：是否只在内测/部分场景启用 2.5，还是一上线就全量？
4. **直连模式**：是否需要让批次直连也跟随（需桌面端发版）？

---

## 附录 A：上游调用示例

### A.1 提交（2.5）
```bash
curl -X POST 'https://api.wuyinkeji.com/api/async/image_gpt_2.5?key=<KEY>' \
  -H 'Authorization: <KEY>' \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"一只柯基在沙滩上奔跑","aspectRatio":"1024x1024","urls":"https://www.xxx.jpg"}'
```
返回：
```json
{ "code": 200, "msg": "成功", "data": { "id": "image_4d39239e-...", "count": 10 },
  "exec_time": 0.29, "ip": "119.6.176.239" }
```

### A.2 轮询（与旧模型一致，无需改）
```bash
curl 'https://api.wuyinkeji.com/api/async/detail?key=<KEY>&id=image_4d39239e-...' \
  -H 'Authorization: <KEY>'
```

## 附录 B：改造后模型决策时序

```
管理员在 wh-admin 选 GPT-Image-2.5 → PATCH /api/credentials/{id} {model:"image_gpt_2.5"}
        │
密钥库 ai-credentials.v1.json：[{kind:"image", active:true, model:"image_gpt_2.5", ciphertext:...}]
        │
用户生图 → POST /api/customer/ai/image {usage_id,prompt,size:"1:1",urls}
        │
_gateway_image_provider() → ("<key>", "image_gpt_2.5")
_gateway_request_hash({... , model:"image_gpt_2.5"})
_submit_server_wuyin(..., model="image_gpt_2.5")
   → POST https://api.wuyinkeji.com/api/async/image_gpt_2.5
     {prompt, aspectRatio:"1024x1024"}
        │
_poll_server_wuyin → /api/async/detail → result_url
        │
结算 model = "image_gpt_2.5"
```
