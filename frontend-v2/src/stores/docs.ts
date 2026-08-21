import { defineStore } from "pinia";
import { docsApi } from "@/api";
import type { Doc } from "@/types/api";

export const useDocsStore = defineStore("docs", {
  state: () => ({
    list: [] as Doc[],
    loading: false,
  }),
  actions: {
    async load() {
      this.loading = true;
      try {
        this.list = await docsApi.list();
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
