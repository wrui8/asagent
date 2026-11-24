import { expect, test } from "@playwright/test";
import { ADMIN, login } from "../helpers";

test("错误密码会显示错误提示且停留在登录页", async ({ page }) => {
  await page.goto("/login");
  await page.getByPlaceholder("wangrui").fill(ADMIN.username);
  await page.locator('input[type="password"]').fill("definitely-wrong-000");
  await page.locator('button[type="submit"]').click();

  await expect(page.locator(".form-error")).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
});

test("管理员登录后进入控制台", async ({ page }) => {
  await login(page);
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator(".user-chip")).toContainText("管理员");
});

test("退出登录后回到登录页", async ({ page }) => {
  await login(page);
  await page.locator(".user-chip").click();
  await page.getByRole("button", { name: "退出登录" }).click();

  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByPlaceholder("wangrui")).toBeVisible();
});
