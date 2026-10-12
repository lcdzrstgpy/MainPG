/**
 * 「完成预检并导出」的请求幂等逻辑。
 *
 * 单独放成纯模块是为了可测：这段逻辑决定「第二次导出会不会被后端判成重复提交」。
 * 后端按 Idempotency-Key 去重，同一个键换一份 payload 会被直接拒绝
 * （`PreviewIdempotencyConflict`），所以键的指纹必须覆盖所有会进请求体的字段。
 */

export type FinalizeDesiredState = {
  product_draft_id: number;
  expected_preview_revision: number;
  expected_result_version: string;
  overrides: Record<string, unknown>;
};

export type FinalizeRequestStorage = {
  read: (key: string) => string | null;
  write: (key: string, value: string) => void;
};

type StoredFinalizeRequest = {
  fingerprint: string;
  idempotencyKey: string;
  items: unknown[];
};

export function stableStringify(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null';
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
  const record = value as Record<string, unknown>;
  const entries = Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableStringify(record[key])}`);
  return `{${entries.join(',')}}`;
}

export async function hashStableValue(value: unknown): Promise<string> {
  const input = stableStringify(value);
  if (globalThis.crypto?.subtle) {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(input));
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  }
  let hash = 2166136261;
  for (let index = 0; index < input.length; index += 1) {
    hash ^= input.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

async function finalizeFingerprint(params: {
  workspaceId: string;
  taskId: number;
  items: FinalizeDesiredState[];
}): Promise<string> {
  return hashStableValue({
    workspaceId: params.workspaceId,
    taskId: params.taskId,
    // ⚠️ expected_preview_revision 必须在指纹里：导出本身会把 revision 推进一格，
    // 漏掉它就会出现「第二次导出的键和第一次完全相同、请求体却不同」，
    // 后端按 key 去重时判定为「同一个键换了另一个请求」而拒绝，
    // 而且因为键是算出来的，清缓存、重新加载都救不回来。
    desiredState: params.items.map(
      ({ product_draft_id, expected_preview_revision, expected_result_version, overrides }) => ({
        product_draft_id,
        expected_preview_revision,
        expected_result_version,
        overrides,
      }),
    ),
  });
}

export async function finalizeIdempotencyKey<T extends FinalizeDesiredState>(params: {
  workspaceId: string;
  taskId: number;
  items: T[];
}): Promise<string> {
  const fingerprint = await finalizeFingerprint(params);
  return `pp-preview-finalize-${params.taskId}-${fingerprint}`;
}

/**
 * 取本次导出要用的幂等键与请求体。
 *
 * 会话里缓存了同一份期望状态（指纹一致）时复用缓存——这样「同一次导出」的重复提交
 * 仍然是幂等的；指纹不一致说明期望状态变了，必须重新算键，否则会拿旧键撞后端冲突。
 */
export async function resolveFinalizeRequest<T extends FinalizeDesiredState>(params: {
  workspaceId: string;
  taskId: number;
  items: T[];
  storageKey: string;
  storage: FinalizeRequestStorage;
}): Promise<{ idempotencyKey: string; items: T[] }> {
  const fingerprint = await finalizeFingerprint(params);
  const stored = params.storage.read(params.storageKey);
  if (stored) {
    try {
      const parsed = JSON.parse(stored) as Partial<StoredFinalizeRequest>;
      if (
        parsed.fingerprint === fingerprint
        && typeof parsed.idempotencyKey === 'string'
        && parsed.idempotencyKey.length > 0
        && Array.isArray(parsed.items)
      ) {
        return { idempotencyKey: parsed.idempotencyKey, items: parsed.items as T[] };
      }
    } catch {
      // 会话里那份坏了就往下重新算。
    }
  }
  const idempotencyKey = `pp-preview-finalize-${params.taskId}-${fingerprint}`;
  const record: StoredFinalizeRequest = { fingerprint, idempotencyKey, items: params.items };
  params.storage.write(params.storageKey, JSON.stringify(record));
  return { idempotencyKey, items: params.items };
}
