import { expect, test } from "@playwright/test";
import { login } from "../helpers";

test("管理员可以生成邀请码", async ({ page }) => {
  await login(page);
  await page.goto("/admin");
  await page.getByRole("button", { name: "生成邀请码" }).first().click();

  const modal = page.locator(".modal");
  await expect(modal.getByText("注册后角色")).toBeVisible();
  await modal.getByRole("button", { name: "生成", exact: true }).click();

  await expect(page.getByText("邀请码已生成")).toBeVisible({ timeout: 15_000 });
});

test("配额读取失败可重试，保存后慢速刷新仍能立即查看新值", async ({ page }) => {
  let phase: "offline" | "retry" | "after-save" = "offline";
  let releaseRetry!: () => void;
  let releaseRefresh!: () => void;
  const retryGate = new Promise<void>((resolve) => { releaseRetry = resolve; });
  const refreshGate = new Promise<void>((resolve) => { releaseRefresh = resolve; });
  await page.route("**/api/users/*/quota", async (route) => {
    if (phase === "offline") return route.fulfill({ status: 503, body: "offline" });
    await (phase === "retry" ? retryGate : refreshGate);
    return route.continue();
  });
  try {
    await login(page);
    await page.goto("/admin");
    await expect(page.locator("th", { hasText: "评测/日" })).toBeVisible();
    const user = page.locator("tr", { hasText: "e2e-user" });
    await user.getByRole("button", { name: "编辑" }).click();
    const modal = page.getByRole("dialog");
    await expect(modal.getByText("编辑用户 · e2e-user")).toBeVisible();
    await expect(modal.getByText("每日评测次数（0 表示不限）")).toBeVisible();
    const limit = modal.locator(".field").filter({ hasText: "每日评测次数" }).locator("input");
    await expect(limit).toBeDisabled();
    await modal.getByRole("checkbox").check();
    const retry = modal.getByRole("button", { name: "重新加载配额" });
    await expect(retry).toBeVisible();
    phase = "retry";
    await retry.click();
    await expect(limit).toBeDisabled();
    releaseRetry();
    await expect(limit).toHaveValue("0");
    await expect(modal.getByRole("checkbox")).toBeChecked();
    await modal.getByRole("checkbox").uncheck();
    await limit.fill("17");
    phase = "after-save";
    const patch = page.waitForRequest((request) => request.method() === "PATCH");
    await modal.getByRole("button", { name: "保存", exact: true }).click();
    expect((await patch).postDataJSON()).toEqual({ role: "user", disabled: false, eval_daily_limit: 17 });
    await user.getByRole("button", { name: "编辑" }).click();
    await expect(limit).toHaveValue("17");
    releaseRefresh();
    await expect(user.locator("td").nth(4)).toHaveText("17");
    await modal.getByRole("button", { name: "取消" }).click();
  } finally {
    releaseRetry();
    releaseRefresh();
  }
});
