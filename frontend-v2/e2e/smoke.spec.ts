import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

/** 收集运行期错误：冒烟同时保证控制台干净 */
function collectErrors(page: import("@playwright/test").Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`console: ${m.text()}`);
  });
  return errors;
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("首屏进入欢迎页：能力卡片 + 示例问题，且无控制台报错", async ({ page }) => {
  const errors = collectErrors(page);
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Multi-Agent 助手" })).toBeVisible();
  await expect(page.getByTestId("welcome-capabilities").getByRole("listitem")).toHaveCount(6);
  await expect(page.getByTestId("welcome-examples").getByRole("button")).toHaveCount(3);
  // 骨架必须收起，否则说明历史加载状态没被复位
  await expect(page.getByTestId("history-skeleton")).toBeHidden();

  expect(errors).toEqual([]);
});

test("发消息后渲染回答与来源，点引用编号高亮对应的 chip", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("welcome-examples").getByRole("button").first().click();

  const answer = page.locator(".msg-in").last();
  await expect(answer).toContainText("对话记录默认保留 180 天");

  // 来源 chip 带序号，序号即引用编号
  const chips = answer.locator("[data-source-index]");
  await expect(chips).toHaveCount(1);
  await expect(chips.first()).toHaveText("1 policies.md ×2");

  // 点 [1] → 第 1 个 chip 高亮（并滚进视野，不被悬浮输入框挡住）
  await answer.locator("[data-cite]").first().click();
  await expect(chips.first()).toHaveClass(/source-chip--highlight/);
});

test("会话列表按 今天/昨天/更早 分组", async ({ page }) => {
  await page.goto("/");
  const aside = page.locator("aside");

  // 同时断言分组存在与顺序（时间倒序）
  await expect(aside.locator("[data-session-group]")).toHaveText(["今天", "昨天", "更早"]);
  await expect(aside.getByText("更早的会话")).toBeVisible();
});

test("打开历史会话：答案与来源一并渲染", async ({ page }) => {
  await page.goto("/");
  await page.locator("aside").getByText("昨天的会话").click();

  const answer = page.locator(".msg-in").last();
  await expect(answer).toContainText("数据与隐私政策");
  await expect(answer.locator("[data-source-index]")).toHaveText("1 policies.md ×2");
});

test("触屏长按会话行弹出操作表", async ({ page }) => {
  await page.goto("/");
  const row = page.locator("aside button").filter({ hasText: "昨天的会话" }).first();

  // 触屏长按：指针类型为 touch，按住超过长按阈值
  await row.dispatchEvent("pointerdown", { pointerType: "touch" });
  await page.waitForTimeout(600);
  await row.dispatchEvent("pointerup", { pointerType: "touch" });

  const menu = page.getByRole("menu");
  await expect(menu).toBeVisible();
  await expect(menu.getByRole("menuitem")).toHaveText(["置顶", "重命名", "删除"]);

  // Esc 关闭，且不会顺带打开会话
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
});
