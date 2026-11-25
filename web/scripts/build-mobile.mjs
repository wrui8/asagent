import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = fileURLToPath(new URL("../", import.meta.url));
const out = join(web, "../app/src/generated/console-html.ts");

// Expo and the shared web UI have separate lockfiles and React versions.
if (process.argv.includes("--install") && !existsSync(join(web, "node_modules/vite/package.json"))) {
  execFileSync(process.execPath, [process.env.npm_execpath, "ci", "--no-audit", "--no-fund"], {
    cwd: web,
    stdio: "inherit",
  });
}

// Metro sets NODE_ENV=development during expo start. The embedded document always
// ships the production React runtime, so the JSX transform must use it as well.
process.env.NODE_ENV = "production";
const { build } = await import("vite");
const bundle = await build({
  root: web,
  mode: "mobile",
  publicDir: false,
  logLevel: "warn",
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  build: {
    write: false,
    target: "safari16.4",
    lib: { entry: join(web, "src/main.tsx"), formats: ["iife"], name: "AsagentConsole" },
    rollupOptions: { output: { inlineDynamicImports: true } },
  },
});
const output = (Array.isArray(bundle) ? bundle[0] : bundle).output;
const js = output.find((item) => item.type === "chunk" && item.isEntry);
const css = output.find((item) => item.type === "asset" && item.fileName.endsWith(".css"));
if (!js || !css || output.length !== 2) {
  throw new Error("Expected a self-contained console with one JavaScript entry and one stylesheet");
}

const script = js.code.replace(/\r\n?/g, "\n").replace(/<\/script/gi, "<\\/script");
const hash = createHash("sha256").update(script).digest("base64");
const policy = `default-src 'none'; script-src 'sha256-${hash}'; style-src 'unsafe-inline'; img-src data: blob: https:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'`;
const html = `<!doctype html>
<html lang="zh-CN" data-theme="dark"><head>
<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${policy}">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>asagent · 控制台</title><style>${css.source}</style></head>
<body><div id="root"></div><script>${script}</script></body></html>`;
const moduleText = `// Generated from web/src by web/scripts/build-mobile.mjs.\nconst html: string = ${JSON.stringify(html)};\nexport default html;\n`;
await mkdir(dirname(out), { recursive: true });
if (!existsSync(out) || (await readFile(out, "utf8")) !== moduleText) {
  await writeFile(out, moduleText);
}
