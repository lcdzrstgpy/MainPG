/**
 * 引导编辑器用的「点选拾取」：进入拾取态后，鼠标移到页面上任意元素即高亮，
 * 点一下就自动生成一个 CSS 选择器，省去手写选择器。
 *
 * 拾取期间在 document 捕获阶段拦截 mousemove / click，并 stopPropagation，
 * 因此被点到的按钮、链接不会被真正触发（React 18 的事件挂在 #root 上，
 * document 捕获阶段先于它执行）。
 */

export type ElementPick = {
  /** 生成的选择器。 */
  selector: string;
  /** 给用户看的元素简述，如 `div.verify-page`。 */
  label: string;
  /** 该选择器在当前页面匹配到的可见元素个数；大于 1 时引导会高亮第一个。 */
  matchCount: number;
};

export type ElementPickerOptions = {
  /** 编辑器面板自身的选择器；落在其中的事件一律忽略，保证面板仍可正常点击。 */
  ignoreSelector: string;
  onPick: (pick: ElementPick) => void;
  onCancel?: () => void;
};

/** 这些类名是运行态标记（选中态、driver.js 自己加的类等），不适合写进选择器。 */
const CLASS_BLOCKLIST = /^(is-|has-|js-|css-|driver-|guide-|active$)/;

const MAX_DEPTH = 6;

function isRendered(element: Element): boolean {
  const rect = element.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

function countVisible(selector: string): number {
  let count = 0;
  for (const node of document.querySelectorAll(selector)) {
    if (isRendered(node)) count += 1;
  }
  return count;
}

function segmentOf(element: Element): string {
  const tag = element.tagName.toLowerCase();
  const classes = Array.from(element.classList)
    .filter((name) => name.length <= 40 && !CLASS_BLOCKLIST.test(name))
    .slice(0, 2)
    .map((name) => `.${CSS.escape(name)}`)
    .join("");
  return `${tag}${classes}`;
}

/**
 * 该元素在同类兄弟节点中的位置段（如 `label:nth-of-type(3)`）。
 * 同类兄弟只有一个时返回 null —— 这种情况加序号没有意义。
 */
function nthSegmentOf(element: Element): string | null {
  const parent = element.parentElement;
  if (!parent) return null;
  const tag = element.tagName.toLowerCase();
  const sameTag = Array.from(parent.children).filter((child) => child.tagName.toLowerCase() === tag);
  if (sameTag.length <= 1) return null;
  return `${tag}:nth-of-type(${sameTag.indexOf(element) + 1})`;
}

/**
 * 从元素自身往上拼类名链，一旦该选择器在全文档只匹配到一个可见元素就停下 ——
 * 与播放时的取值约定一致（详见 GuideTour 里的 visible()）。
 * 类名链撞车时（多个同构控件共用同一串 class，例如采集表单里并排的三个下拉），
 * 自下而上给某一层补 `:nth-of-type(序号)` 消歧，保证生成的仍是唯一选择器。
 * 都试过仍不唯一时返回最长的那份，播放时高亮第一个可见命中。
 */
export function buildSelector(element: Element): string {
  if (element.id) {
    const byId = `#${CSS.escape(element.id)}`;
    if (countVisible(byId) === 1) return byId;
  }

  // chain[0] 是最外层祖先，末尾是元素自身，正好是后代选择器的书写顺序。
  const chain: Element[] = [];
  let current: Element | null = element;
  while (current && current !== document.body && chain.length < MAX_DEPTH) {
    chain.unshift(current);
    const candidate = chain.map(segmentOf).join(" ");
    if (countVisible(candidate) === 1) return candidate;
    current = current.parentElement;
  }

  for (let depth = chain.length - 1; depth >= 0; depth -= 1) {
    const nth = nthSegmentOf(chain[depth]);
    if (!nth) continue;
    const candidate = [
      ...chain.slice(0, depth).map(segmentOf),
      nth,
      ...chain.slice(depth + 1).map(segmentOf),
    ].join(" ");
    if (countVisible(candidate) === 1) return candidate;
  }

  return chain.map(segmentOf).join(" ") || "body";
}

export function describeElement(element: Element): string {
  return segmentOf(element);
}

export function countVisibleMatch(selector: string): number {
  return countVisible(selector);
}

/** 开始拾取，返回一个取消函数（重复调用无副作用）。 */
export function startElementPicker({ ignoreSelector, onPick, onCancel }: ElementPickerOptions): () => void {
  const box = document.createElement("div");
  box.className = "guide-pick-box";
  box.innerHTML = '<span class="guide-pick-box-label"></span>';
  const label = box.querySelector(".guide-pick-box-label") as HTMLSpanElement;
  document.body.appendChild(box);
  document.body.classList.add("guide-picking");

  let hovered: Element | null = null;
  let stopped = false;
  let frame = 0;

  const isIgnored = (element: Element | null): boolean =>
    !!element && !!element.closest(ignoreSelector);

  const targetAt = (event: MouseEvent): Element | null => {
    const element = document.elementFromPoint(event.clientX, event.clientY);
    if (!element || isIgnored(element) || element === box) return null;
    return element;
  };

  const paint = (element: Element | null) => {
    if (!element) {
      box.style.display = "none";
      return;
    }
    const rect = element.getBoundingClientRect();
    box.style.display = "block";
    box.style.left = `${rect.left}px`;
    box.style.top = `${rect.top}px`;
    box.style.width = `${rect.width}px`;
    box.style.height = `${rect.height}px`;
    label.textContent = buildSelector(element);
  };

  const schedulePaint = (event: MouseEvent) => {
    // 用 rAF 合并高频 mousemove，避免每帧多次 layout 计算。
    if (frame) return;
    frame = window.requestAnimationFrame(() => {
      frame = 0;
      const element = targetAt(event);
      if (element !== hovered) hovered = element;
      paint(hovered);
    });
  };

  const handleMove = (event: MouseEvent) => {
    if (isIgnored(event.target as Element | null)) {
      hovered = null;
      paint(null);
      return;
    }
    schedulePaint(event);
  };

  const handleClick = (event: MouseEvent) => {
    const clicked = event.target as Element | null;
    if (isIgnored(clicked)) return;

    const element = targetAt(event);
    // 一律吞掉事件：即使这一下点空了，也不该真的触发页面上的按钮。
    event.preventDefault();
    event.stopPropagation();
    if (!element) return;

    const selector = buildSelector(element);
    const pick: ElementPick = {
      selector,
      label: describeElement(element),
      matchCount: countVisible(selector),
    };
    stop();
    onPick(pick);
  };

  const handleKeyDown = (event: KeyboardEvent) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    event.stopPropagation();
    stop();
    onCancel?.();
  };

  const handleScroll = () => paint(hovered);

  function stop() {
    if (stopped) return;
    stopped = true;
    if (frame) window.cancelAnimationFrame(frame);
    document.removeEventListener("mousemove", handleMove, true);
    document.removeEventListener("click", handleClick, true);
    document.removeEventListener("keydown", handleKeyDown, true);
    window.removeEventListener("scroll", handleScroll, true);
    document.body.classList.remove("guide-picking");
    box.remove();
  }

  document.addEventListener("mousemove", handleMove, true);
  document.addEventListener("click", handleClick, true);
  document.addEventListener("keydown", handleKeyDown, true);
  window.addEventListener("scroll", handleScroll, true);

  return stop;
}
