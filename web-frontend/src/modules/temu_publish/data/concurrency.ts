/**
 * 限并发地跑异步任务，结果按输入顺序返回。
 *
 * 用途：提交发布前要把轮播 / 外包装 / 详情图逐张传到 Temu 图床。
 * 原来是一张一张串行传（10 张图就要等 10 个来回），全部并发又担心
 * 打爆图床限流 —— 这里用固定并发数（默认 3）折中。
 *
 * 语义：
 * - 顺序保持：返回数组和输入一一对应，不会因为哪个先完成就错位
 * - 一个失败即整体失败（Promise.all 语义），由调用方统一报错
 * - 空数组 / limit<=0 直接返回空，不会挂死
 */
export async function mapWithConcurrency<T, R>(
  items: T[],
  limit: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const size = Math.max(1, Math.floor(limit));
  const results = new Array<R>(items.length);
  let cursor = 0;
  const runners = Array.from({ length: Math.min(size, items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await worker(items[index], index);
    }
  });
  await Promise.all(runners);
  return results;
}
