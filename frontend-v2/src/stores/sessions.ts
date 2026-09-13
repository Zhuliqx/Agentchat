import { defineStore } from "pinia";
import { sessionsApi } from "@/api";
import type { Session } from "@/types/api";

export const useSessionsStore = defineStore("sessions", {
  state: () => ({
    list: [] as Session[],
    currentId: "",
    batchMode: false,
    selected: new Set<string>(),
    loading: false,
    /** 最近一次加载失败的原因（成功后清空），供界面提示与重试 */
    error: null as string | null,
  }),
  getters: {
    current: (s): Session | null => s.list.find((x) => x.id === s.currentId) || null,
  },
  actions: {
    async load() {
      this.loading = true;
      try {
        this.list = await sessionsApi.list();
        this.error = null;
      } catch (e) {
        // 不向上抛：失败原因留在 error 里交给界面提示，旧列表继续可用
        this.error = (e as Error).message || "加载会话失败";
      } finally {
        this.loading = false;
      }
    },
    async create() {
      const s = await sessionsApi.create();
      this.list.unshift(s);
      this.currentId = s.id;
      return s;
    },
    async rename(id: string, title: string) {
      const s = await sessionsApi.rename(id, title);
      const idx = this.list.findIndex((x) => x.id === id);
      if (idx >= 0) this.list[idx].title = s.title;
    },
    async pin(id: string, pinned: boolean) {
      const s = await sessionsApi.pin(id, pinned);
      const idx = this.list.findIndex((x) => x.id === id);
      if (idx >= 0) {
        this.list[idx].pinned = s.pinned;
        // 与服务端同一口径排序（置顶在前，其余按更新时间倒序）：
        // 只按 pinned 稳定排序的话，取消置顶后这条会留在列表最上方，回不到原位
        this.list.sort((a, b) => {
          const pa = a.pinned ? 1 : 0;
          const pb = b.pinned ? 1 : 0;
          if (pa !== pb) return pb - pa;
          return Date.parse(b.updated_at || "") - Date.parse(a.updated_at || "");
        });
      }
    },
    async remove(id: string) {
      await sessionsApi.remove(id);
      this.list = this.list.filter((x) => x.id !== id);
      if (this.currentId === id) this.currentId = this.list[0]?.id || "";
    },
    async batchDelete(ids: string[]) {
      await sessionsApi.batchDelete(ids);
      const removed = new Set(ids);
      this.list = this.list.filter((x) => !removed.has(x.id));
      this.selected.clear();
      if (this.currentId && removed.has(this.currentId)) {
        this.currentId = this.list[0]?.id || "";
      }
    },
    toggleBatch() {
      this.batchMode = !this.batchMode;
      if (!this.batchMode) this.selected.clear();
    },
    toggleSelect(id: string) {
      if (this.selected.has(id)) this.selected.delete(id);
      else this.selected.add(id);
    },
    toggleSelectAll() {
      const all = this.list.every((x) => this.selected.has(x.id));
      if (all) this.selected.clear();
      else this.list.forEach((x) => this.selected.add(x.id));
    },
  },
});
