import { defineStore } from "pinia";

export type DialogKind = "alert" | "confirm" | "prompt";

interface DialogState {
  kind: DialogKind;
  title: string;
  message: string;
  value: string;
  resolve: (value?: unknown) => void;
}

/** 全局应用弹窗：替代原生 alert/confirm/prompt，保证主题与键盘体验一致。 */
export const useDialogStore = defineStore("dialog", {
  state: () => ({
    current: null as DialogState | null,
  }),
  actions: {
    alert(message: string, title = "提示"): Promise<void> {
      return new Promise((resolve) => {
        this.current = {
          kind: "alert",
          title,
          message,
          value: "",
          resolve: () => resolve(),
        };
      });
    },
    confirm(message: string, title = "确认"): Promise<boolean> {
      return new Promise((resolve) => {
        this.current = {
          kind: "confirm",
          title,
          message,
          value: "",
          resolve: (value?: unknown) => resolve(Boolean(value)),
        };
      });
    },
    prompt(message: string, defaultValue = "", title = "输入"): Promise<string | null> {
      return new Promise((resolve) => {
        this.current = {
          kind: "prompt",
          title,
          message,
          value: defaultValue,
          resolve: (value?: unknown) =>
            resolve(value === null || value === undefined ? null : String(value)),
        };
      });
    },
    /** 统一的失败提示：把异常整理成「<动作>：<原因>」，避免各处各写一套 */
    async alertError(prefix: string, e: unknown): Promise<void> {
      const reason = e instanceof Error && e.message ? e.message : String(e ?? "未知错误");
      await this.alert(`${prefix}：${reason}`, "操作失败");
    },
    resolve(value?: unknown) {
      const current = this.current;
      this.current = null;
      current?.resolve(value);
    },
  },
});
