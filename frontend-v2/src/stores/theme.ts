import { defineStore } from "pinia";

export type ThemeMode = "dark" | "light";

export const useThemeStore = defineStore("theme", {
  state: () => ({
    mode: (localStorage.getItem("theme") as ThemeMode) || "dark",
  }),
  actions: {
    init() {
      this.apply();
    },
    toggle() {
      this.mode = this.mode === "dark" ? "light" : "dark";
      localStorage.setItem("theme", this.mode);
      this.apply();
    },
    set(mode: ThemeMode) {
      this.mode = mode;
      localStorage.setItem("theme", mode);
      this.apply();
    },
    apply() {
      document.documentElement.classList.toggle("light", this.mode === "light");
    },
  },
});
