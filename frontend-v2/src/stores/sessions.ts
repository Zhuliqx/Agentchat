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
  }),
  getters: {
    current: (s): Session | null =>
      s.list.find((x) => x.id === s.currentId) || null,
  },
  actions: {
    async load() {
      this.loading = true;
      try {
        this.list = await sessionsApi.list();
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
