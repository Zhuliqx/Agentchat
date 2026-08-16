// Markdown 渲染：marked（官方） + highlight.js 语法高亮 + DOMPurify 消毒
import { marked } from "marked";
import { markedHighlight } from "marked-highlight";
// 按需注册常用语言，避免全量引入（bundle 从 ~1.2MB 降到 ~450KB）
import hljs from "highlight.js/lib/core";
import javascript from "highlight.js/lib/languages/javascript";
import typescript from "highlight.js/lib/languages/typescript";
import python from "highlight.js/lib/languages/python";
import bash from "highlight.js/lib/languages/bash";
import json from "highlight.js/lib/languages/json";
import css from "highlight.js/lib/languages/css";
import xml from "highlight.js/lib/languages/xml";
import sql from "highlight.js/lib/languages/sql";
import markdown from "highlight.js/lib/languages/markdown";
import plaintext from "highlight.js/lib/languages/plaintext";
import DOMPurify from "dompurify";

hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("python", python);
hljs.registerLanguage("bash", bash);
hljs.registerLanguage("shell", bash);
hljs.registerLanguage("json", json);
hljs.registerLanguage("css", css);
hljs.registerLanguage("xml", xml);
hljs.registerLanguage("html", xml);
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("markdown", markdown);
hljs.registerLanguage("plaintext", plaintext);

export function createMarkdownRenderer() {
  marked.use(
    markedHighlight({
      langPrefix: "hljs language-",
      highlight(code, lang) {
        if (lang && hljs.getLanguage(lang)) {
          return hljs.highlight(code, { language: lang }).value;
        }
        return hljs.highlightAuto(code).value;
      },
    })
  );

  marked.setOptions({
    breaks: true,
    gfm: true,
  });

  return {
    /** 渲染为安全 HTML */
    render(src: string): string {
      const raw = marked.parse(String(src ?? ""), { async: false }) as string;
      return DOMPurify.sanitize(raw);
    },
    /** 流式渲染：未闭合代码块（奇数个 ```）时把最后一个未配对的围栏转义为文本，避免内容被吞进代码块 */
    renderStream(src: string): string {
      const text = String(src ?? "");
      const fences: number[] = [];
      let from = 0;
      let idx = -1;
      while ((idx = text.indexOf("```", from)) !== -1) {
        fences.push(idx);
        from = idx + 3;
      }
      if (fences.length % 2 === 1) {
        const cut = fences[fences.length - 1];
        return this.render(text.slice(0, cut) + "&#96;&#96;&#96;" + text.slice(cut + 3));
      }
      return this.render(text);
    },
  };
}

export type MarkdownRenderer = ReturnType<typeof createMarkdownRenderer>;

// 全局单例：marked.use 是全局配置，多次调用会重复注册插件
export const md = createMarkdownRenderer();

