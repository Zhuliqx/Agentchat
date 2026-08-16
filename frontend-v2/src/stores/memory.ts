import { defineStore } from "pinia";
import { memoryApi } from "@/api";
import type { Memory } from "@/types/api";

export const useMemoryStore = defineStore("memory", {
  state: () => ({
    list: [] as Memory[],
    loading: false,
  }),
  actions: {
    async load(query = "") {
      this.loading = true;
      try {
        this.list = await memoryApi.list(query);
      } finally {
        this.loading = false;
      }
    },
    async add(content: string) {
      await memoryApi.add(content);
      await this.load();
    },
    async remove(id: string) {
      await memoryApi.remove(id);
      await this.load();
    },
  },
});
