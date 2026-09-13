import { describe, expect, it } from "vitest";
import { normalizeSources, sourceName, sourceTitle } from "@/utils/sources";

describe("normalizeSources", () => {
  it("新格式：保留路径与命中片段数", () => {
    expect(
      normalizeSources([
        { path: "/kb/a.md", hits: 3 },
        { path: "/kb/b.md", hits: 1 },
      ]),
    ).toEqual([
      { path: "/kb/a.md", hits: 3 },
      { path: "/kb/b.md", hits: 1 },
    ]);
  });

  it("老格式：只有路径字符串时命中数缺省", () => {
    expect(normalizeSources(["/kb/a.md"])).toEqual([{ path: "/kb/a.md" }]);
  });

  it("忽略空值与非法项", () => {
    expect(normalizeSources(["", null, 42, { path: "" }, { hits: 2 }, "/kb/ok.md"])).toEqual([
      { path: "/kb/ok.md" },
    ]);
    expect(normalizeSources(undefined)).toEqual([]);
    expect(normalizeSources("not-array")).toEqual([]);
  });
});

describe("sourceName / sourceTitle", () => {
  it("文件名支持 Windows 反斜杠路径", () => {
    expect(sourceName("D:\\桌面\\Agentchat\\data\\kb\\company.md")).toBe("company.md");
    expect(sourceName("/srv/data/policies.md")).toBe("policies.md");
  });

  it("命中多段时提示里带上数量", () => {
    expect(sourceTitle({ path: "/kb/a.md", hits: 3 })).toBe("/kb/a.md（命中 3 段）");
    expect(sourceTitle({ path: "/kb/a.md", hits: 1 })).toBe("/kb/a.md");
    expect(sourceTitle({ path: "/kb/a.md" })).toBe("/kb/a.md");
  });
});
