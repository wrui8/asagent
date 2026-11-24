import { expect, test } from "@playwright/test";
import { USER, login, sidebar } from "../helpers";

test("管理员侧边栏包含评测、系统与用户管理", async ({ page }) => {
  await login(page);
  await expect(sidebar(page).getByText("评测", { exact: true })).toBeVisible();
  await expect(sidebar(page).getByText("MCP 与系统")).toBeVisible();
  await expect(sidebar(page).getByText("用户与邀请码")).toBeVisible();
});

test("普通用户看不到管理员入口，直接访问会被拒绝", async ({ page }) => {
  await login(page, USER);
  await expect(sidebar(page).getByText("知识库")).toBeVisible();
  await expect(sidebar(page).getByText("MCP 与系统")).toHaveCount(0);

  await page.goto("/evals");
  await expect(page.getByText("需要管理员权限")).toBeVisible();

  await page.goto("/system");
  await expect(page.getByText("需要管理员权限")).toBeVisible();

  await page.goto("/admin");
  await expect(page.getByText("只对管理员开放")).toBeVisible();
});

test("普通用户可以访问审计页", async ({ page }) => {
  await login(page, USER);
  await page.goto("/audit");
  // 普通用户没有会话时展示空态，有会话时展示「审计范围」，两者都说明页面正常开放
  await expect(page.getByText("审计范围").or(page.getByText("还没有可审计的会话"))).toBeVisible();
});
