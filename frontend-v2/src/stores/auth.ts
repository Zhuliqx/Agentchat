import { defineStore } from "pinia";
import { authApi } from "@/api";
import {
  getToken,
  setToken,
  getStoredUser,
  setStoredUser,
  clearAuth,
} from "@/api/token";
import type { User } from "@/types/api";

export const useAuthStore = defineStore("auth", {
  state: () => ({
    token: getToken(),
    user: getStoredUser() as User | null,
    menuOpen: false,
    authOpen: false,
    authTab: "login" as "login" | "register",
    loading: false,
  }),
  getters: {
    isLoggedIn: (s) => !!s.token,
  },
  actions: {
    /** 启动时恢复登录态（token 失效则清除） */
    async init() {
      if (!this.token) return;
      try {
        this.user = await authApi.me();
        setStoredUser(this.user);
      } catch {
        this.logoutLocal();
      }
    },
    openAuth(tab: "login" | "register" = "login") {
      this.authTab = tab;
      this.authOpen = true;
    },
    async login(username: string, password: string) {
      this.loading = true;
      try {
        const data = await authApi.login(username, password);
        this.token = data.token;
        this.user = data.user;
        setToken(data.token);
        setStoredUser(data.user);
        this.authOpen = false;
      } finally {
        this.loading = false;
      }
    },
    async register(username: string, password: string) {
      await authApi.register(username, password);
    },
    logoutLocal() {
      this.token = "";
      this.user = null;
      clearAuth();
    },
    toggleMenu() {
      this.menuOpen = !this.menuOpen;
    },
    closeMenu() {
      this.menuOpen = false;
    },
  },
});
