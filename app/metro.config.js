const { execFileSync } = require("node:child_process");
const path = require("node:path");
const { getDefaultConfig } = require("expo/metro-config");

// Direct Expo CLI calls (including export and native builds) must use current web sources.
execFileSync(process.execPath, [path.join(__dirname, "../web/scripts/build-mobile.mjs")], {
  stdio: "inherit",
});
module.exports = getDefaultConfig(__dirname);
