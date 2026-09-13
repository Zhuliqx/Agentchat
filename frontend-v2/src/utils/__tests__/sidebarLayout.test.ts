import { describe, expect, it } from "vitest";
import {
  clampSidebarWidth,
  SIDEBAR_DEFAULT_WIDTH,
  SIDEBAR_MAX_WIDTH,
  SIDEBAR_MIN_WIDTH,
} from "@/utils/sidebarLayout";

describe("clampSidebarWidth", () => {
  it("脏数据回落到默认宽度", () => {
    expect(clampSidebarWidth(null)).toBe(SIDEBAR_DEFAULT_WIDTH);
    expect(clampSidebarWidth("abc")).toBe(SIDEBAR_DEFAULT_WIDTH);
    expect(clampSidebarWidth(0)).toBe(SIDEBAR_DEFAULT_WIDTH);
  });

  it("越界值钳制到上下限", () => {
    expect(clampSidebarWidth(50)).toBe(SIDEBAR_MIN_WIDTH);
    expect(clampSidebarWidth(9999)).toBe(SIDEBAR_MAX_WIDTH);
  });

  it("区间内取整保留", () => {
    expect(clampSidebarWidth("260.6")).toBe(261);
  });
});
