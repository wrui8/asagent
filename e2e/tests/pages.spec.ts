import { expect, test } from "@playwright/test";
import { login } from "../helpers";

test("评测中心展示场景、KPI 与启动入口", async ({ page }) => {
  await login(page);
  await page.goto("/evals");

  await expect(page.getByText("场景与评测集")).toBeVisible();
  await expect(page.getByText("可用场景")).toBeVisible();
  await expect(page.getByText("今日评测")).toBeVisible();
  await expect(page.getByText("SQL 数据分析").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: "开始评测" }).first()).toBeVisible();
});

test("审计页展示筛选与空态", async ({ page }) => {
  await login(page);
  await page.goto("/audit");

  await expect(page.getByText("审计范围")).toBeVisible();
  // 种子库没有会话与写操作，审计表不渲染，只显示空态
  await expect(page.getByText("没有写操作记录")).toBeVisible({ timeout: 15_000 });
});

test("系统页展示运行路径与 MCP 配置", async ({ page }) => {
  await login(page);
  await page.goto("/system");

  await expect(page.getByText("运行路径")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("MCP 工具服务").first()).toBeVisible();
  await expect(page.getByText("mcp__demo__*").first()).toBeVisible({
    timeout: 15_000,
  });
});
