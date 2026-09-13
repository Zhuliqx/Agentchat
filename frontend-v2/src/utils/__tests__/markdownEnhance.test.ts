import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  decorateCitations,
  decorateMarkdown,
  handleCodeCopyClick,
  highlightSourceChip,
  syncTypingCaret,
} from "@/utils/markdownEnhance";

function makeRoot(html: string) {
  const root = document.createElement("div");
  root.className = "md";
  root.innerHTML = html;
  document.body.appendChild(root);
  return root;
}

describe("markdownEnhance", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("给代码块加复制按钮、给表格套横向滚动容器，且重复调用不重复包裹", () => {
    const root = makeRoot(
      "<pre><code>print(1)</code></pre><table><tbody><tr><td>x</td></tr></tbody></table>",
    );

    decorateMarkdown(root);
    decorateMarkdown(root); // 流式输出会反复调用

    expect(root.querySelectorAll("[data-code-block]")).toHaveLength(1);
    expect(root.querySelectorAll("[data-copy-code]")).toHaveLength(1);
    expect(root.querySelectorAll(".md-table-wrap")).toHaveLength(1);
    expect(root.querySelector(".md-table-wrap > table")).not.toBeNull();
  });

  it("点击复制按钮把代码写进剪贴板并给出反馈", async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const root = makeRoot("<pre><code>print(1)</code></pre>");
    decorateMarkdown(root);

    const button = root.querySelector<HTMLElement>("[data-copy-code]")!;
    const handled = await handleCodeCopyClick(button);

    expect(handled).toBe(true);
    expect(writeText).toHaveBeenCalledWith("print(1)");
    expect(button.textContent).toBe("已复制");
  });

  it("点击非复制按钮不处理", async () => {
    const root = makeRoot("<p>正文</p>");
    expect(await handleCodeCopyClick(root.querySelector("p"))).toBe(false);
  });

  it("打字光标插进最后一个文本块内部（容器级 ::after 会多出一行）", () => {
    const root = makeRoot("<p>第一段</p><ul><li>列表项</li></ul>");

    syncTypingCaret(root, true);

    const caret = root.querySelector("[data-typing-caret]");
    expect(caret).not.toBeNull();
    expect(caret!.parentElement?.tagName).toBe("LI"); // 落进最后一个块，而不是容器
    expect(root.children).toHaveLength(2); // 没有为光标新增元素

    syncTypingCaret(root, false);
    expect(root.querySelector("[data-typing-caret]")).toBeNull();
  });

  it("重复调用不会堆积多个光标", () => {
    const root = makeRoot("<p>正文</p>");
    syncTypingCaret(root, true);
    syncTypingCaret(root, true);
    expect(root.querySelectorAll("[data-typing-caret]")).toHaveLength(1);
  });

  it("引用编号：范围内的 [n] 变成可点击标记，越界编号删除、代码块不动", () => {
    const root = makeRoot(
      "<p>结论见 [1] 与 [2]，还有一个不存在的 [9]。</p><pre><code>arr[1]</code></pre>",
    );

    decorateCitations(root, 2);

    const marks = root.querySelectorAll("[data-cite]");
    expect(marks).toHaveLength(2);
    expect(Array.from(marks).map((m) => m.textContent)).toEqual(["[1]", "[2]"]);
    // 越界编号没有可指向的来源，连同前导空格一起删掉
    expect(root.textContent).not.toContain("[9]");
    expect(root.textContent).toContain("还有一个不存在的。");
    expect(root.querySelector("code")?.querySelector("[data-cite]")).toBeNull();
    expect(root.querySelector("code")?.textContent).toBe("arr[1]");
  });

  it("没有来源列表时（如联网搜索的回答）正文里的 [n] 直接清掉", () => {
    const root = makeRoot("<p>OpenAI 推出 Astra [1]，Anthropic 发布 Fable 5.1 [4]。</p>");

    decorateCitations(root, 0);

    expect(root.querySelectorAll("[data-cite]")).toHaveLength(0);
    expect(root.textContent).toBe("OpenAI 推出 Astra，Anthropic 发布 Fable 5.1。");
  });

  it("点击引用编号：滚动到对应来源 chip 并加高亮类", () => {
    const row = document.createElement("div");
    row.className = "msg-in";
    row.innerHTML = `
      <div class="md"><p>见 [2]</p></div>
      <a data-source-index="1" href="#">a.md</a>
      <a data-source-index="2" href="#">b.md</a>`;
    document.body.appendChild(row);
    const md = row.querySelector<HTMLElement>(".md")!;
    decorateCitations(md, 2);

    highlightSourceChip(md, 2);

    const chip = row.querySelector('[data-source-index="2"]')!;
    expect(chip.classList.contains("source-chip--highlight")).toBe(true);
    expect(
      row.querySelector('[data-source-index="1"]')!.classList.contains("source-chip--highlight"),
    ).toBe(false);
  });
});
