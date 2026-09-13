/**
 * Markdown 渲染结果增强：给代码块加复制按钮、给表格套横向滚动容器。
 *
 * v-html 每次重新渲染都会重建 DOM，所以这两件事必须在渲染后调用；
 * 用 data 标记做幂等保护，流式输出期间反复调用也不会重复包裹。
 */

const COPY_BTN_CLASS =
  "absolute right-2 top-2 rounded-md border border-line-2 bg-surface-2/90 px-2 py-0.5 text-2xs text-ink-dim opacity-0 transition hover:text-ink focus:opacity-100 group-hover/code:opacity-100";

/** 光标可以落进去的最后一个块：必须是能在行内追加内容的元素 */
const CARET_HOSTS = "p, li, h1, h2, h3, h4, h5, h6, blockquote, td, th, code";

/** 引用编号形如 [1]；只处理正文文本节点，跳过代码块与已有链接 */
const CITATION_TEST = /\[\d+\]/;
const CITATION_RE = /\[(\d+)\]/g;

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
 * 引用编号联动：把正文里的 [n] 换成可点击标记，点击后高亮下方第 n 个来源。
 * sourceCount 用于挡掉越界编号（模型可能写出不存在的来源号）。
 */
export function decorateCitations(root: HTMLElement | null, sourceCount: number): void {
  if (!root || sourceCount <= 0) return;
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
    let replaced = false;
    CITATION_RE.lastIndex = 0; // matchAll 会读 lastIndex，先归零避免漏匹配
    for (const match of text.matchAll(CITATION_RE)) {
      const index = Number(match[1]);
      if (index < 1 || index > sourceCount) continue; // 越界编号原样保留
      const start = match.index ?? 0;
      frag.append(text.slice(cursor, start));
      const mark = document.createElement("button");
      mark.type = "button";
      mark.dataset.cite = String(index);
      mark.className = "citation-mark";
      mark.textContent = match[0];
      frag.append(mark);
      cursor = start + match[0].length;
      replaced = true;
    }
    if (!replaced) continue;
    frag.append(text.slice(cursor));
    node.replaceWith(frag);
  }
}

/** 点击引用编号：滚动到对应来源 chip 并短暂高亮 */
export function highlightSourceChip(root: HTMLElement | null, index: number): void {
  const row = root?.closest(".msg-in");
  const chip = row?.querySelector<HTMLElement>(`[data-source-index="${index}"]`);
  if (!chip) return;
  // jsdom 等环境没有 scrollIntoView，做一次存在性判断
  chip.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  chip.classList.add("source-chip--highlight");
  setTimeout(() => chip.classList.remove("source-chip--highlight"), 1200);
}
