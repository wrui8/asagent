import { expect, test } from "@playwright/test";
import { login } from "../helpers";

test("新建知识文档后出现在列表，并可移入回收站", async ({ page }) => {
  await login(page);
  await page.goto("/kb");
  await expect(page.getByText("知识库").first()).toBeVisible();

  await page.getByRole("button", { name: "新建文档" }).click();
  const modal = page.locator(".modal");
  await expect(modal.getByText("新建知识文档")).toBeVisible();

  await modal.getByPlaceholder("faq").fill("e2e-smoke-doc");
  await modal.getByPlaceholder("留空则取正文首个标题").fill("E2E 冒烟文档");
  await modal
    .getByPlaceholder(/# 标题/)
    .fill("# E2E 冒烟文档\n\n这是 Playwright 写入了知识库的冒烟内容。");
  await modal.getByRole("button", { name: "保存文档" }).click();

  await expect(page.getByText("已保存。重建索引后即可被 RAG 检索")).toBeVisible({
    timeout: 20_000,
  });
  const row = page.locator("tr", { hasText: "E2E 冒烟文档" });
  await expect(row).toBeVisible();

  page.on("dialog", (dialog) => void dialog.accept());
  await row.getByTitle("移入回收站").click();
  await expect(page.getByText("已移入回收站，并从向量索引中移除")).toBeVisible({
    timeout: 20_000,
  });

  // 列表默认显示回收站，删除后行仍在但状态为「已删除」
  await expect(row.getByText("已删除")).toBeVisible();
  await page.getByRole("checkbox", { name: "显示回收站" }).uncheck();
  await expect(page.locator("tr", { hasText: "E2E 冒烟文档" })).toHaveCount(0);
});
