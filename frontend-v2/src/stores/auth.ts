import { defineStore } from "pinia";
import { authApi } from "@/api";
import {
  getRefreshToken,
  getToken,
  setRefreshToken,
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
    platformOperator: null as boolean | null,
  }),
  getters: {
    isLoggedIn: (s) => !!s.token,
  },
  actions: {
    /** 查询当前身份是否具备平台操作员能力（决定模型切换/任务入口显隐） */
    async loadCapabilities() {
      try {
        const res = await authApi.capabilities();
        this.platformOperator = res.platform_operator;
      } catch {
        this.platformOperator = null;
      }
    },
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
        setRefreshToken(data.refresh_token);
        setStoredUser(data.user);
        this.authOpen = false;
        await this.loadCapabilities();
      } finally {
        this.loading = false;
      }
    },
    async register(username: string, password: string) {
      await authApi.register(username, password);
    },
    /** 通知后端撤销 refresh（尽力而为），随后清空本地登录态 */
    async logout() {
      const refreshToken = getRefreshToken();
      if (refreshToken) {
        try {
          await authApi.logout(refreshToken);
        } catch {
          /* 后端不可达也继续本地登出 */
        }
      }
      this.logoutLocal();
    },
    logoutLocal() {
      this.token = "";
      this.user = null;
      this.platformOperator = null;
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
