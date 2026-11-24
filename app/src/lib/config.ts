/**
 * 服务地址在构建时写死，最终用户不可修改。
 * 本地联调可用 EXPO_PUBLIC_API_BASE 覆盖（Expo 在打包时内联）。
 */
const BUILT_IN_API_BASE = "https://agent.woaiwyj.xin";

export const API_BASE = (
  process.env.EXPO_PUBLIC_API_BASE || BUILT_IN_API_BASE
).replace(/\/+$/, "");
