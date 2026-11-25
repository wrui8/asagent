# asagent mobile

Expo SDK 58 移动客户端，接入 `asagent web` 的多用户 API。iOS 和浏览器预览直接使用 `web/src` 的控制台页面、主题及权限逻辑。iOS 将页面随应用打包，在 WKWebView 中显示，登录、API 请求、复制和轨迹文件分享经原生层执行。

```bash
npm install
npx expo start                     # Expo Go（真机走公网 API 地址）
npx expo start --web --port 8082   # 浏览器预览
npx tsc --noEmit                   # 类型检查
```

- API 地址构建时写死：`src/lib/config.ts`（联调用 `EXPO_PUBLIC_API_BASE` 覆盖）
- 原生端 refresh token 存 SecureStore，Web 预览降级 localStorage
- 本地 Web 预览需后端设置 `AS_ALLOWED_ORIGINS=http://localhost:8082`
- iOS 和 Web 预览的页面入口在 `../web/src/App.tsx`。会话、对话、知识库和审计位于手机底栏。管理员点右上角账号菜单可以进入评测、MCP 与系统、用户与邀请码。Android 继续使用 `src/app/` 下的 React Native 页面。
- `npm ci` 安装依赖时会构建共享控制台，Metro 启动时会重新打包。开发中修改 `web/src` 后，在 `app` 目录运行 `npm run console:build` 即可更新正在运行的移动预览。
- 构建需 Node ≥ 22.13（Expo SDK 58 要求）

## 在 Windows 上预览 iPhone 客户端

在 VS Code 中打开当前分支的 `app` 目录，在 PowerShell 运行以下命令。没有 iPhone 时，最后一条命令会在浏览器中打开同一套移动端界面，可在开发者工具中切换到窄屏视口。

```powershell
npm.cmd ci
npx.cmd expo start --web --port 8082
```

使用本地后端时，先把 `EXPO_PUBLIC_API_BASE` 设为该后端地址，确认其 `/api/health` 返回 `{"status":"ok"}`，并使用该环境签发的账号或邀请码。浏览器预览还需要后端允许预览页的实际来源，例如 `http://localhost:8082`。

有 iPhone 时，在电脑上运行 `npx.cmd expo login` 及 `npx.cmd expo start --go`，手机的 Expo Go 登录同一 Expo 账号后扫码。手机也要能直接访问 ASAgent 后端。当前 Expo SDK 58 支持 iOS 16.4 起的设备。[Expo 真机预览](https://docs.expo.dev/get-started/start-developing/) · [Expo SDK 58](https://docs.expo.dev/versions/v58.0.0/)

开发回归在 `app` 下执行 `npm.cmd test`、`npx.cmd tsc --noEmit`、`npm.cmd run lint` 和 `npx.cmd expo export --platform ios`。在 `e2e` 下安装 npm 依赖和 Playwright 的 Chromium、WebKit 后，运行 `npm.cmd run test:mobile` 可用独立测试库验证两种角色的手机操作。[iPhone 开发计划与验收记录](../docs/superpowers/plans/2026-11-25-ios-app.md)
