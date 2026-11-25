import { expect, test } from "@playwright/test";
import { USER } from "../helpers";

test("手机会话续接增量轨迹并核对断线的审批结果", async ({ page }) => {
  const id = "e2e-mobile-approval";
  const base = `/api/sessions/${id}`;
  const marker = "完整审批参数的末尾";
  const approval = {
    call_id: "write-once", tool: "write_document", permission: 2, status: "pending",
    args: { path: "approval.md", content: "a".repeat(700) + marker },
  };
  const answer = "**P1** 故障要求 15 分钟响应。";
  const event = (seq: number, type: string, payload: Record<string, unknown>) => ({
    seq, type, payload, session_id: id, ts: "2026-11-25T12:00:00",
  });
  const events = [
    event(1, "tool_call", { call_id: approval.call_id, tool: approval.tool }),
    event(2, "approval_required", { call_id: approval.call_id, tool: approval.tool }),
    event(3, "approval_decided", { call_id: approval.call_id, approved: true }),
    event(4, "final", { text: answer }),
  ];
  let status = "running";
  let approvalsSubmitted = 0;
  const cursors: [string, number][] = [];
  await page.route((url) => url.pathname === base || url.pathname.startsWith(`${base}/`), async (route) => {
    const url = new URL(route.request().url());
    const suffix = url.pathname.slice(base.length);
    const method = route.request().method();
    const headers = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "authorization, content-type, x-client",
    };
    const fulfill = (json: unknown) => route.fulfill({ json, headers });
    if (method === "OPTIONS") {
      await route.fulfill({ status: 204, headers });
    } else if (suffix === "/approvals" && method === "POST") {
      expect(route.request().postDataJSON()).toEqual({
        call_id: approval.call_id, approved: true, resume: true,
      });
      approvalsSubmitted += 1;
      status = "done";
      await route.abort("failed");
    } else if (suffix === "/approvals") {
      // The approvals read can still contain the pre-commit row when the decision event is visible.
      await fulfill(status === "running" ? [] : [approval]);
    } else if (suffix === "/events") {
      const cursor = Number(url.searchParams.get("after_seq"));
      cursors.push([status, cursor]);
      const last = status === "running" ? 1 : status === "awaiting_approval" ? 2 : cursor < 3 ? 3 : 4;
      await fulfill(events.filter(({ seq }) => cursor < seq && seq <= last));
    } else if (suffix === "/messages") {
      await fulfill([{ role: "user", content: "生成测试文档" }]);
    } else {
      await fulfill({ session_id: id, title: "手机审批验证", profile: "office", status, step: 1,
        cost: 0, final_answer: status === "done" ? answer : null });
    }
  });

  await page.goto(`/#/sessions/${id}`);
  const ui = page.frameLocator("iframe");
  await ui.getByPlaceholder("wangrui").fill(USER.username);
  await ui.locator('input[type="password"]').fill(USER.password);
  await ui.locator('button[type="submit"]').click();
  await expect(ui.getByRole("heading", { name: "手机审批验证" })).toBeVisible();
  status = "awaiting_approval";
  await page.evaluate(() => {
    document.querySelector("iframe")?.contentWindow?.postMessage({
      channel: "asagent.mobile.v1", event: "active",
    }, location.origin);
  });
  await expect(ui.getByText(marker, { exact: false })).toHaveCount(0);
  await ui.getByRole("button", { name: "查看完整参数" }).click();
  await expect(ui.getByText(marker, { exact: false })).toBeVisible();
  await ui.getByRole("button", { name: "批准并继续" }).click();
  await expect(ui.getByText("已核实该调用已批准")).toBeVisible();
  await expect(ui.getByRole("button", { name: "批准并继续" })).toHaveCount(0);
  await expect(ui.locator("strong", { hasText: "P1" })).toBeVisible();

  await ui.getByRole("button", { name: "执行轨迹", exact: true }).click();
  await ui.getByRole("button", { name: "JSON", exact: true }).click();
  const trace = JSON.parse((await ui.locator(".trace-json").textContent()) ?? "[]");
  expect(trace.map(({ seq }: { seq: number }) => seq)).toEqual([1, 2, 3, 4]);
  expect(cursors).toContainEqual(["awaiting_approval", 1]);
  expect(cursors).toContainEqual(["done", 3]);
  expect(approvalsSubmitted).toBe(1);
});
