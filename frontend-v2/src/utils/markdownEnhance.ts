/**
 * Markdown 渲染结果增强：给代码块加复制按钮、给表格套横向滚动容器。
 *
 * v-html 每次重新渲染都会重建 DOM，所以这两件事必须在渲染后调用；
 * 用 data 标记做幂等保护，流式输出期间反复调用也不会重复包裹。
 */

const COPY_BTN_CLASS =
  "absolute right-2 top-2 rounded-md border border-line-2 bg-surface-2/90 px-2 py-0.5 text-2xs text-ink-dim opacity-0 transition hover:text-ink focus:opacity-100 group-hover/code:opacity-100 coarse:px-3 coarse:py-1.5";

/** 光标可以落进去的最后一个块：必须是能在行内追加内容的元素 */
const CARET_HOSTS = "p, li, h1, h2, h3, h4, h5, h6, blockquote, td, th, code";

/** 引用编号：[1] 为主，模型也写过 [来源 1] / 【来源 1｜本次第 2 位】；只处理正文文本节点，跳过代码块与链接 */
const CITATION_TEST = /\[\d+\]|\[来源\s*\d+[^\]]*\]|【来源\s*\d+[^】]*】/;
const CITATION_RE = /\[(\d+)\]|\[来源\s*(\d+)[^\]]*\]|【来源\s*(\d+)[^】]*】/g;

export function decorateMarkdown(root: HTMLElement | null): void {
  if (!root) return;

  root.querySelectorAll("pre").forEach((pre) => {
    if (pre.closest("[data-code-block]")) return;
    const wrap = document.createElement("div");
    wrap.dataset.codeBlock = "";
    wrap.className = "relative group/code";
    pre.replaceWith(wrap);
    wrap.appendChild(pre);

    const button = document.createElement("button");
    button.type = "button";
    button.dataset.copyCode = "";
    button.className = COPY_BTN_CLASS;
    button.textContent = "复制";
    wrap.appendChild(button);
  });

  root.querySelectorAll("table").forEach((table) => {
    if (table.closest(".md-table-wrap")) return;
    const wrap = document.createElement("div");
    wrap.className = "md-table-wrap";
    table.replaceWith(wrap);
    wrap.appendChild(table);
  });
}

/** 代码块复制（事件委托调用）：命中复制按钮返回 true。 */
export async function handleCodeCopyClick(target: EventTarget | null): Promise<boolean> {
  if (!(target instanceof HTMLElement)) return false;
  const button = target.closest<HTMLElement>("[data-copy-code]");
  if (!button) return false;
  const text = button.parentElement?.querySelector("code")?.textContent ?? "";
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = "已复制";
    setTimeout(() => (button.textContent = "复制"), 1500);
  } catch {
    /* 剪贴板不可用（无权限/非安全上下文）时静默忽略 */
  }
  return true;
}

/**
 * 打字光标同步。
 *
 * 挂在容器上的 `::after` 会因为容器里有块级子元素而生成匿名块 —— 每次流式都多出
 * 一整行（实测 31px），流式结束后该行消失，整段内容瞬间上移，看起来就是"界面跳动"。
 * 所以光标插入最后一个文本块内部，行内追加、不改变任何高度。
 */
export function syncTypingCaret(root: HTMLElement | null, streaming: boolean): void {
  if (!root) return;
  root.querySelectorAll("[data-typing-caret]").forEach((el) => el.remove());
  if (!streaming) return;

  const hosts = root.querySelectorAll<HTMLElement>(CARET_HOSTS);
  const host = hosts.length ? hosts[hosts.length - 1] : root;
  const caret = document.createElement("span");
  caret.dataset.typingCaret = "";
  caret.className = "typing-caret-inline";
  host.appendChild(caret);
}

/**
 * 引用编号处理：范围内的 [n] 换成可点击标记（点击高亮下方第 n 个来源）；
 * 悬空编号（越界，或整条消息没有来源列表——如联网搜索的回答）直接删除，
 * 否则正文里会留下无指向的 "[1][4]" 噪音。
 */
export function decorateCitations(root: HTMLElement | null, sourceCount: number): void {
  if (!root) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (parent?.closest("pre, code, a, [data-cite]")) return NodeFilter.FILTER_REJECT;
      return CITATION_TEST.test(node.nodeValue || "")
        ? NodeFilter.FILTER_ACCEPT
        : NodeFilter.FILTER_REJECT;
    },
  });
  const targets: Text[] = [];
  while (walker.nextNode()) targets.push(walker.currentNode as Text);

  for (const node of targets) {
    const text = node.nodeValue || "";
    const frag = document.createDocumentFragment();
    let cursor = 0;
    let changed = false;
    CITATION_RE.lastIndex = 0; // matchAll 会读 lastIndex，先归零避免漏匹配
    for (const match of text.matchAll(CITATION_RE)) {
      const index = Number(match[1] ?? match[2] ?? match[3]);
      const start = match.index ?? 0;
      const end = start + match[0].length;
      if (index < 1 || index > sourceCount) {
        // 悬空编号：连同紧邻的前导空格一起去掉
        const cut = start > cursor && text[start - 1] === " " ? start - 1 : start;
        frag.append(text.slice(cursor, cut));
        cursor = end;
        changed = true;
        continue;
      }
      frag.append(text.slice(cursor, start));
      const mark = document.createElement("button");
      mark.type = "button";
      mark.dataset.cite = String(index);
      mark.className = "citation-mark";
      mark.textContent = match[0];
      frag.append(mark);
      cursor = end;
      changed = true;
    }
    if (!changed) continue;
    frag.append(text.slice(cursor));
    node.replaceWith(frag);
  }
}

/** 点击引用编号：滚到对应来源 chip（避开悬浮输入框）并短暂高亮 */
export function highlightSourceChip(root: HTMLElement | null, index: number): void {
  const row = root?.closest(".msg-in");
  const chip = row?.querySelector<HTMLElement>(`[data-source-index="${index}"]`);
  if (!chip) return;
  scrollChipIntoView(chip);
  chip.classList.add("source-chip--highlight");
  // 留够时间：平滑滚动到位后高亮还在，用户才能把正文里的编号和这个 chip 对上
  setTimeout(() => chip.classList.remove("source-chip--highlight"), 1600);
}

/**
 * 让来源 chip 真正落在视野里。
 *
 * scrollIntoView({block:"nearest"}) 只按滚动容器的可视框判断，而输入框是悬浮在会话区
 * 底部的：chip 处在它后面时会被判成"已可见"，于是一个像素都不滚，用户点完什么也看不到。
 * 所以按容器与输入框的真实位置算差值，只在需要时滚那一点。
 */
function scrollChipIntoView(chip: HTMLElement): void {
  const container = chip.closest<HTMLElement>("[data-msg-scroll]");
  if (!container) return;
  const box = container.getBoundingClientRect();
  const rect = chip.getBoundingClientRect();
  const composerTop = document
    .querySelector<HTMLElement>("[data-composer]")
    ?.getBoundingClientRect().top;
  const gap = 8;
  const down = rect.bottom + gap - Math.min(composerTop ?? box.bottom, box.bottom);
  const up = box.top + gap - rect.top;
  const delta = down > 0 ? down : -Math.max(up, 0);
  if (delta) container.scrollBy?.({ top: delta, behavior: "smooth" });
}
