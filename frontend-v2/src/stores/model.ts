import { defineStore } from "pinia";
import { modelsApi } from "@/api";
import type { ModelInfo } from "@/types/api";

/** 运行时模型切换 store：加载可用模型列表 + 当前选择，切换持久化到后端。 */
export const useModelStore = defineStore("model", {
  state: () => ({
    models: [] as ModelInfo[],
    currentId: "" as string, // 当前模型 id（与后端 current 匹配）
    loaded: false,
  }),
  getters: {
    currentLabel(s): string {
      const m = s.models.find((x) => x.id === s.currentId);
      return m ? m.label : "模型";
    },
    currentShort(s): string {
      const m = s.models.find((x) => x.id === s.currentId);
      return m ? m.model : "";
    },
  },
  actions: {
    /** 由后端返回的 current 反查 id（按 provider+model 匹配） */
    _resolveId(current: { provider: string; model: string } | null): string {
      if (!current) return "";
      return (
        this.models.find(
          (m) => m.provider === current.provider && m.model === current.model
        )?.id || ""
      );
    },
    async load() {
      try {
        const res = await modelsApi.list();
        this.models = res.models;
        this.currentId = this._resolveId(res.current);
      } catch {
        /* 网络/后端不可用时静默 */
      } finally {
        this.loaded = true;
      }
    },
    async set(modelId: string) {
      if (modelId === this.currentId) return;
      const prev = this.currentId;
      // 乐观更新
      this.currentId = modelId;
      try {
        const res = await modelsApi.setCurrent(modelId);
        this.models = res.models;
        this.currentId = this._resolveId(res.current);
      } catch {
        this.currentId = prev; // 失败回滚
      }
    },
  },
});
