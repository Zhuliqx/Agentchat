/* 前端入口：负责启动初始化（UI 逻辑在 app.js，核心在 core.js，滚动条在 scrollbar.js）。 */
import { init } from "./app.js";
import { $ } from "./core.js";

init().catch((e) => {
  console.error(e);
  $("#health-text").textContent = "初始化失败：" + e.message;
  $("#health-dot").className = "dot err";
});
