import { defineStore } from "pinia";

/** 对话能力开关（知识库 RAG / 联网搜索 / 长期记忆），跨组件共享。
 * 知识库开关在侧边栏文档栏，记忆开关在侧边栏记忆栏，联网开关在输入框工具条。
 */
export const useChatOptionsStore = defineStore("chatOptions", {
  state: () => ({
    useRag: true,
    useSearch: true,
    useMemory: true,
  }),
});
