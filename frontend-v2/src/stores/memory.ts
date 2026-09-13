import { defineStore } from "pinia";
import { memoryApi } from "@/api";
import type { Memory } from "@/types/api";

export const useMemoryStore = defineStore("memory", {
  state: () => ({
    list: [] as Memory[],
    loading: false,
    /** 最近一次加载失败的原因（成功后清空），供界面提示与重试 */
    error: null as string | null,
  }),
  actions: {
    async load(query = "") {
      this.loading = true;
      try {
        this.list = await memoryApi.list(query);
        this.error = null;
      } catch (e) {
        this.error = (e as Error).message || "加载记忆失败";
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
