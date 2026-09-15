import { defineStore } from "pinia";

export type ThemeMode = "dark" | "light";

/** 暗/亮主题对应的浏览器 UI 颜色（移动端地址栏） */
const THEME_COLOR: Record<ThemeMode, string> = { dark: "#0b0d11", light: "#f6f7f9" };

/** 没手动选过主题时跟随系统偏好（与 index.html 的首屏脚本同一口径） */
function systemMode(): ThemeMode {
  try {
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export const useThemeStore = defineStore("theme", {
  state: () => {
    const saved = localStorage.getItem("theme") as ThemeMode | null;
    return {
      mode: saved || systemMode(),
      /** 是否仍在跟随系统：手动切换后就固定下来 */
      followsSystem: !saved,
    };
  },
  actions: {
    init() {
      this.apply();
      // 跟随系统期间，系统主题变化要实时同步（手动选过就不再响应）
      try {
        const mq = window.matchMedia("(prefers-color-scheme: light)");
        mq.addEventListener("change", (e) => {
          if (!this.followsSystem) return;
          this.mode = e.matches ? "light" : "dark";
          this.apply();
        });
      } catch {
        /* 无 matchMedia 时忽略 */
      }
    },
    toggle() {
      this.set(this.mode === "dark" ? "light" : "dark");
    },
    set(mode: ThemeMode) {
      this.mode = mode;
      this.followsSystem = false;
      localStorage.setItem("theme", mode);
      this.apply();
    },
    apply() {
      const light = this.mode === "light";
      const root = document.documentElement;
      root.classList.toggle("light", light);
      root.style.colorScheme = light ? "light" : "dark";
      document
        .querySelector('meta[name="theme-color"]')
        ?.setAttribute("content", THEME_COLOR[this.mode]);
    },
  },
});
