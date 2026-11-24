import { expect, test } from "@playwright/test";
import { login } from "../helpers";

test("用户表展示评测配额列与编辑字段", async ({ page }) => {
  await login(page);
  await page.goto("/admin");
  // Playwright 对未带 scope 的 <th> 计算为 cell，这里直接按表头文本定位
  await expect(page.locator("th", { hasText: "评测/日" })).toBeVisible();

  const userRow = page.locator("tr", { hasText: "e2e-user" });
  await userRow.getByRole("button", { name: "编辑" }).click();
  const modal = page.locator(".modal");
  await expect(modal.getByText("编辑用户 · e2e-user")).toBeVisible();
  await expect(modal.getByText("每日评测次数（0 表示不限）")).toBeVisible();
  await modal.getByRole("button", { name: "取消" }).click();
});

test("管理员可以生成邀请码", async ({ page }) => {
  await login(page);
  await page.goto("/admin");
  await page.getByRole("button", { name: "生成邀请码" }).first().click();

  const modal = page.locator(".modal");
  await expect(modal.getByText("注册后角色")).toBeVisible();
  await modal.getByRole("button", { name: "生成", exact: true }).click();

  await expect(page.getByText("邀请码已生成")).toBeVisible({ timeout: 15_000 });
});
