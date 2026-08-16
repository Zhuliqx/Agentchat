import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import { useThemeStore } from "@/stores/theme";
import "./style.css";

const app = createApp(App);
app.use(createPinia());
// 初始化主题（读写 localStorage 并应用到 <html> 类）
useThemeStore().init();
app.mount("#app");
