import { describe, expect, it } from "vitest";
import { md } from "@/utils/markdown";

describe("markdown render", () => {
  it("renders heading and list", () => {
    const html = md.render("# 标题\n\n- 项目一\n- 项目二");
    expect(html).toContain("<h1");
    expect(html).toContain("标题");
    expect(html).toContain("<li>项目一</li>");
  });

  it("renders table", () => {
    const html = md.render("| a | b |\n|---|---|\n| 1 | 2 |");
    expect(html).toContain("<table");
    expect(html).toContain("<td>1</td>");
  });

  it("highlights fenced code with language", () => {
    const html = md.render("```python\nprint('hi')\n```");
    expect(html).toContain("hljs");
    expect(html).toContain("language-python");
  });

  it("sanitizes script injection", () => {
    const html = md.render("<script>alert(1)</script>");
    expect(html).not.toContain("<script");
  });
});

describe("stream render (unclosed code fence)", () => {
  it("strips trailing unclosed fence so rest is not swallowed", () => {
    const html = md.renderStream("```python\nprint(1)\n```\n\n还有一段文本");
    expect(html).toContain("还有一段文本");
  });

  it("keeps text after unclosed fence as plain text", () => {
    const html = md.renderStream("```\ndef foo():\n    pass");
    // 未闭合代码块：不应把后续内容吞成整块代码
    expect(html).toContain("def foo()");
  });

  it("leaves complete fenced block intact", () => {
    const html = md.renderStream("```js\nconst a = 1;\n```");
    expect(html).toContain("hljs");
  });
});
