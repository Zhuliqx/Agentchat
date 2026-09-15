import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useThemeStore } from "@/stores/theme";

/** 模拟 prefers-color-scheme 并暴露手动触发 change 的入口 */
function mockMatchMedia(matches: boolean) {
  const listeners: ((e: { matches: boolean }) => void)[] = [];
  const mq = {
    matches,
    addEventListener: (_event: string, cb: (e: { matches: boolean }) => void) => {
      listeners.push(cb);
    },
    removeEventListener: vi.fn(),
  };
  window.matchMedia = vi.fn(() => mq) as unknown as typeof window.matchMedia;
  return { fire: (value: boolean) => listeners.forEach((cb) => cb({ matches: value })) };
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.className = "";
  document.documentElement.style.colorScheme = "";
  setActivePinia(createPinia());
});

describe("主题 store", () => {
  it("没手动选过时跟随系统偏好", () => {
    mockMatchMedia(true); // 系统亮色
    const theme = useThemeStore();

    expect(theme.mode).toBe("light");
    expect(theme.followsSystem).toBe(true);
    theme.init();
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.style.colorScheme).toBe("light");
  });

  it("手动选择优先于系统偏好", () => {
    localStorage.setItem("theme", "dark");
    mockMatchMedia(true); // 系统亮色，但用户选过暗色
    const theme = useThemeStore();

    expect(theme.mode).toBe("dark");
    expect(theme.followsSystem).toBe(false);
  });

  it("toggle 后固定下来并写入 localStorage", () => {
    mockMatchMedia(false); // 系统暗色
    const theme = useThemeStore();
    theme.init();

    theme.toggle();

    expect(theme.mode).toBe("light");
    expect(theme.followsSystem).toBe(false);
    expect(localStorage.getItem("theme")).toBe("light");
    expect(document.documentElement.classList.contains("light")).toBe(true);
  });

  it("跟随系统期间响应系统切换；手动选过后不再响应", () => {
    const media = mockMatchMedia(false);
    const theme = useThemeStore();
    theme.init();
    expect(theme.mode).toBe("dark");

    media.fire(true); // 系统切到亮色
    expect(theme.mode).toBe("light");

    theme.set("dark"); // 手动固定暗色
    media.fire(true);
    expect(theme.mode).toBe("dark");
  });
});
