import { defineConfig, devices } from "@playwright/test";

/**
 * 前端 E2E 冒烟：用 route mock 掉后端（不需要跑 Postgres/Milvus/LLM），
 * 只验证界面装配与交互——首屏、欢迎页、引用联动、会话分组、长按操作表。
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://127.0.0.1:4174",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  // 跑生产构建产物：与 FastAPI 托管时的形态一致
  webServer: {
    // 显式 --host 127.0.0.1：vite 默认只监听 localhost，可能只解析到 ::1
    command: "npm run build && npm run preview -- --port 4174 --strictPort --host 127.0.0.1",
    url: "http://127.0.0.1:4174",
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
