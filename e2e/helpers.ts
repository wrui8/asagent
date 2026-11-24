import { expect, type Page } from "@playwright/test";

export interface Account {
  username: string;
  password: string;
}

// 与 scripts/seed_e2e.py 保持一致（仅测试账号）
export const ADMIN: Account = {
  username: "e2e-admin",
  password: "e2e-admin-pass-2026",
};
export const USER: Account = {
  username: "e2e-user",
  password: "e2e-user-pass-2026",
};

export async function login(page: Page, account: Account = ADMIN): Promise<void> {
  await page.goto("/login");
  await page.getByPlaceholder("wangrui").fill(account.username);
  await page.locator('input[type="password"]').fill(account.password);
  await page.locator('button[type="submit"]').click();
  await expect(page.locator(".user-chip")).toContainText(account.username);
}

export function sidebar(page: Page) {
  return page.locator(".sidebar");
}
