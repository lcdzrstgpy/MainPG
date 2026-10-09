/**
 * 反馈内容的预填合并逻辑。
 *
 * 从操作答疑跳过来时，把原问题**追加**到已有内容后面，不覆盖用户已经打了一半的字。
 * 组件（FeedbackPanel）与测试共用同一条规则，避免两边行为漂移。
 */
export function mergePrefillContent(current: string, incoming: string): string {
  const next = incoming.trim();
  if (!next) return current;
  if (current.includes(next)) return current;
  return current ? `${current}\n\n${next}` : next;
}
