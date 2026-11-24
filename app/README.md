# asagent mobile

Expo SDK 58 + expo-router 移动端，接入 `asagent web` 的多用户 API。

```bash
npm install
npx expo start                     # Expo Go（真机走公网 API 地址）
npx expo start --web --port 8082   # 浏览器预览
npx tsc --noEmit                   # 类型检查
```

- API 地址构建时写死：`src/lib/config.ts`（联调用 `EXPO_PUBLIC_API_BASE` 覆盖）
- 原生端 refresh token 存 SecureStore，Web 预览降级 localStorage
- 本地 Web 预览需后端设置 `AS_ALLOWED_ORIGINS=http://localhost:8082`
- 页面：`src/app/login.tsx`、`index.tsx`（会话列表）、`new.tsx`、`session/[id].tsx`
- 构建需 Node ≥ 22.13（Expo SDK 58 要求）

完整部署与验收说明见仓库根 `README.md`。
