import { defineStore } from "pinia";
import { ref, watch } from "vue";

/** 对话能力开关（知识库 RAG / 联网搜索 / 长期记忆），跨组件共享。
 * 知识库开关在侧边栏文档栏，记忆开关在侧边栏记忆栏，联网开关在输入框工具条。
 *
 * 持久化到 localStorage：否则刷新页面后回到默认 true（联网开关尤其关键，
 * 用户关掉后刷新又变回开，导致请求仍带 use_search=true）。
 */
const STORAGE_KEY = "chat-options";

interface Options {
  useRag: boolean;
  useSearch: boolean;
  useMemory: boolean;
}

function loadDefaults(): Options {
  const d: Options = { useRag: true, useSearch: true, useMemory: true };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const p = JSON.parse(raw) as Partial<Options>;
      if (typeof p.useRag === "boolean") d.useRag = p.useRag;
      if (typeof p.useSearch === "boolean") d.useSearch = p.useSearch;
      if (typeof p.useMemory === "boolean") d.useMemory = p.useMemory;
    }
  } catch {
    /* 解析失败则用默认值 */
  }
  return d;
}

export const useChatOptionsStore = defineStore("chatOptions", () => {
  const init = loadDefaults();
  const useRag = ref(init.useRag);
  const useSearch = ref(init.useSearch);
  const useMemory = ref(init.useMemory);

  watch(
    [useRag, useSearch, useMemory],
    () => {
      try {
        localStorage.setItem(
          STORAGE_KEY,
          JSON.stringify({
            useRag: useRag.value,
            useSearch: useSearch.value,
            useMemory: useMemory.value,
          }),
        );
      } catch {
        /* 存储失败忽略 */
      }
    },
    { immediate: true },
  );

  return { useRag, useSearch, useMemory };
});
