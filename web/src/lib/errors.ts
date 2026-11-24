import { ApiError } from "./api";

/** 把异常转成面向用户的中文提示。 */
export function errorMessage(error: unknown, fallback = "操作失败，请稍后重试"): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) {
    if (error.name === "AbortError") return "请求已取消";
    return error.message || fallback;
  }
  return fallback;
}
