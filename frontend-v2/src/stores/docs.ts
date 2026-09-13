import { defineStore } from "pinia";
import { docsApi } from "@/api";
import type { Doc } from "@/types/api";

export const useDocsStore = defineStore("docs", {
  state: () => ({
    list: [] as Doc[],
    loading: false,
    /** 最近一次加载失败的原因（成功后清空），供界面提示与重试 */
    error: null as string | null,
  }),
  actions: {
    async load() {
      this.loading = true;
      try {
        this.list = await docsApi.list();
        this.error = null;
      } catch (e) {
        this.error = (e as Error).message || "加载文档失败";
      } finally {
        this.loading = false;
      }
    },
    async upload(files: File[]) {
      const r = await docsApi.upload(files);
      return r.tasks;
    },
    async remove(source: string) {
      await docsApi.remove(source);
      await this.load();
    },
    async removeMany(sources: string[]) {
      if (!sources.length) return;
      await docsApi.batchRemove(sources);
      await this.load();
    },
    async setTag(source: string, tag: string | null) {
      await docsApi.setTag(source, tag);
      await this.load();
    },
  },
});
