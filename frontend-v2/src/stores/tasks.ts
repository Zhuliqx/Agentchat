import { defineStore } from "pinia";
import { tasksApi } from "@/api";
import type { Task, TaskRegistryItem } from "@/types/api";

export const useTasksStore = defineStore("tasks", {
  state: () => ({
    list: [] as Task[],
    registry: [] as TaskRegistryItem[],
    loading: false,
  }),
  actions: {
    async load() {
      this.loading = true;
      try {
        const [list, registry] = await Promise.all([
          tasksApi.list(),
          tasksApi.registry(),
        ]);
        this.list = list;
        this.registry = registry;
      } finally {
        this.loading = false;
      }
    },
    async create(name: string, taskType: string, schedule: string) {
      await tasksApi.create(name, taskType, schedule);
      await this.load();
    },
    async update(id: string, patch: { name?: string; schedule?: string; enabled?: boolean }) {
      await tasksApi.update(id, patch);
      await this.load();
    },
    async remove(id: string) {
      await tasksApi.remove(id);
      await this.load();
    },
    async run(id: string) {
      await tasksApi.run(id);
      await this.load();
    },
  },
});
