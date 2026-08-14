# AI 对话输入与 7 天保留 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持粘贴图片、Enter 发送、自动滚动，并在加载 AI 会话时真实删除超过 7 天的本地会话与附件。

**Architecture:** 前端在 `AiServicePage` 复用既有 `selectImage` 上传通道，补充粘贴、键盘与滚动控制；上传中的图片会阻止发送。后端在查询会话前按当前工作区和用户清理 `created_at` 早于七天前的会话，并扩展会话删除的资产收集范围，使其覆盖消息附件、普通创作输出和 POD 输出。

**Tech Stack:** React 18、TypeScript、CSS、Node.js、Python 3.10+、SQLite、pytest、Vite。

## Global Constraints

- 不增加第三方运行时依赖。
- Enter 发送，Shift+Enter 换行，组合输入期间不得发送。
- 只删除当前工作区与当前用户创建超过 7×24 小时的本地会话及其关联资产。
- 不修改模型网关或现有附件 API。

---

### Task 1: 后端清理过期会话及其资产

**Files:**

- Modify: `local-runtime/wh_local/modules/ai_service/service.py: AiService.list_conversations`
- Modify: `local-runtime/wh_local/modules/ai_service/tests/test_service.py`

**Interfaces:**

- Consumes: `AiService.delete_conversation(actor, conversation_id)`、`ai_service_conversations.created_at`、`ai_service_creations.output_asset_ids_json` 和 `ai_service_pod_groups.output_asset_ids_json`。
- Produces: `AiService.purge_expired_conversations(actor, now: datetime | None = None) -> int`，在 `list_conversations` 中调用。

- [x] **Step 1: Write the failing service tests**

添加一个测试，创建两个用户的会话和资产，给第一个用户的会话写入消息附件、普通创作输出和 POD 输出，再将该会话的 `created_at`、`updated_at` 更新为八天前，然后执行 `list_conversations`：

```python
assert [item["conversation_id"] for item in service.list_conversations(actor)] == [recent["conversation_id"]]
assert all(not path.exists() for path in expired_asset_paths)
assert service.list_conversations(other_actor)[0]["conversation_id"] == other_expired["conversation_id"]
```

再添加边界测试，创建时间恰好在七天内的会话仍在列表中。

- [x] **Step 2: Run the focused tests and verify RED**

Run: `PYTHONPATH=local-runtime /Applications/anaconda3/bin/python3.12 -m pytest local-runtime/wh_local/modules/ai_service/tests/test_service.py -q`

Expected: FAIL because expired conversations remain and `purge_expired_conversations` does not exist.

- [x] **Step 3: Implement minimal expiration cleanup**

扩展 `delete_conversation`：读取消息的 `asset_ids_json`、创作表的 `output_asset_ids_json` 及该会话创作对应 POD 分组的 `output_asset_ids_json`，合并为去重的资产 ID 集合后再删除资产记录与文件。再添加服务方法：

```python
def purge_expired_conversations(self, actor: Actor, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=7)
    with self._connect() as conn:
        rows = conn.execute(
            """SELECT conversation_id FROM ai_service_conversations
               WHERE workspace_id = ? AND owner_user_id = ? AND created_at < ?""",
            (actor.workspace_id, actor.id, cutoff.isoformat(timespec="seconds")),
        ).fetchall()
    for row in rows:
        self.delete_conversation(actor, row["conversation_id"])
    return len(rows)
```

并在 `list_conversations` 的查询前调用它；从 `datetime` 导入 `timedelta`。

- [x] **Step 4: Run the focused tests and verify GREEN**

Run: `PYTHONPATH=local-runtime /Applications/anaconda3/bin/python3.12 -m pytest local-runtime/wh_local/modules/ai_service/tests/test_service.py -q`

Expected: all tests pass.

### Task 2: 提取并验证对话输入事件规则

**Files:**

- Create: `web-frontend/src/modules/ai_service/data/chatComposerEvents.ts`
- Create: `web-frontend/src/modules/ai_service/data/chatComposerEvents.test.ts`

**Interfaces:**

- Produces: `pastedImageFile(items: DataTransferItemList, now?: number) -> File | undefined` and `shouldSendOnEnter(event: Pick<KeyboardEvent, "key" | "shiftKey" | "isComposing">) -> boolean`.
- Consumed by: `AiServicePage` textarea event handlers.

- [x] **Step 1: Write failing unit tests**

用 Web API stub 覆盖以下规则：

```ts
assert.equal(shouldSendOnEnter({ key: "Enter", shiftKey: false, isComposing: false }), true);
assert.equal(shouldSendOnEnter({ key: "Enter", shiftKey: true, isComposing: false }), false);
assert.equal(shouldSendOnEnter({ key: "Enter", shiftKey: false, isComposing: true }), false);
assert.equal(pastedImageFile(items)?.type, "image/png");
assert.equal(pastedImageFile(textItems), undefined);
```

- [x] **Step 2: Run the test and verify RED**

Run: `npx tsx --test src/modules/ai_service/data/chatComposerEvents.test.ts`

Expected: FAIL because the module is absent.

- [x] **Step 3: Implement the pure helpers**

```ts
export function shouldSendOnEnter(event: Pick<KeyboardEvent, "key" | "shiftKey" | "isComposing">) {
  return event.key === "Enter" && !event.shiftKey && !event.isComposing;
}

export function pastedImageFile(items: DataTransferItemList, now = Date.now()) {
  const image = Array.from(items).find((item) => item.kind === "file" && item.type.startsWith("image/"));
  const file = image?.getAsFile();
  return file ? new File([file], `pasted-image-${now}.${file.type.split("/")[1] || "png"}`, { type: file.type }) : undefined;
}
```

- [x] **Step 4: Run the test and verify GREEN**

Run: `npx tsx --test src/modules/ai_service/data/chatComposerEvents.test.ts`

Expected: all assertions pass.

### Task 3: 接入粘贴、发送与自动滚动

**Files:**

- Modify: `web-frontend/src/modules/ai_service/pages/AiServicePage.tsx`

**Interfaces:**

- Consumes: `pastedImageFile`, `shouldSendOnEnter`，既有 `selectImage` 和 `generate`。
- Produces: 聊天输入框的 `onPaste`、`onKeyDown` 和消息流 `ref`；上传期间统一禁发。

- [x] **Step 1: Extend the failing source-level regression check**

在 `web-frontend/scripts/check-ai-session-sidebar-overflow.mjs` 新增读取 `AiServicePage.tsx` 的断言，要求聊天 textarea 具有 `onPaste` 和 `onKeyDown`，消息流具有 `ref={conversationFlowRef}`，且发送禁用条件包含 `isUploadingImage`。

- [x] **Step 2: Run the regression check and verify RED**

Run: `node scripts/check-ai-session-sidebar-overflow.mjs`

Expected: FAIL because the required input handlers, flow ref and upload guard are absent.

- [x] **Step 3: Implement the minimal page behavior**

在 `AiServicePage` 中：

```tsx
const [isUploadingImage, setIsUploadingImage] = useState(false);
const conversationFlowRef = useRef<HTMLDivElement>(null);

useEffect(() => {
  conversationFlowRef.current?.scrollTo({ top: conversationFlowRef.current.scrollHeight, behavior: "smooth" });
}, [messages, isGenerating, podJob]);
```

在 `selectImage` 的本地保存请求前后设置 `isUploadingImage`；在 textarea 中，当 `mode === "chat"` 时拦截粘贴图片并调用 `selectImage`，以及用 `shouldSendOnEnter` 在符合发送条件时 `preventDefault()` 后调用 `generate()`。发送按钮禁用条件与 `generate` 入口均增加 `isUploadingImage`，消息流 div 加上 `ref={conversationFlowRef}`。

- [x] **Step 4: Run the regression check and frontend build**

Run: `node scripts/check-ai-session-sidebar-overflow.mjs && npm run build`

Expected: regression check、TypeScript check 和 Vite build 全部通过。

### Task 4: 全量验证并提交

**Files:**

- Modify: `docs/superpowers/plans/2026-08-13-ai-service-chat-composer-and-retention.md`

- [x] **Step 1: Run all changed-area tests**

Run: `PYTHONPATH=local-runtime /Applications/anaconda3/bin/python3.12 -m pytest local-runtime/wh_local/modules/ai_service/tests -q && cd web-frontend && node --experimental-strip-types --test src/modules/ai_service/data/chatComposerEvents.test.ts && node scripts/check-ai-session-sidebar-overflow.mjs && npm run build`

Expected: all commands exit 0.

- [x] **Step 2: Inspect the final diff**

Run: `git diff --check && git diff --stat`

Expected: no whitespace errors; only AI service implementation, tests, check and plan files are changed.

- [x] **Step 3: Commit**

```bash
git add local-runtime/wh_local/modules/ai_service/service.py local-runtime/wh_local/modules/ai_service/tests/test_service.py web-frontend/src/modules/ai_service/data/chatComposerEvents.ts web-frontend/src/modules/ai_service/data/chatComposerEvents.test.ts web-frontend/src/modules/ai_service/pages/AiServicePage.tsx web-frontend/src/modules/ai_service/styles/aiService.css web-frontend/scripts/check-ai-session-sidebar-overflow.mjs docs/superpowers/plans/2026-08-13-ai-service-chat-composer-and-retention.md
git commit -m "feat(ai-service): streamline chat input and retention"
```
