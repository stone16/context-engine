import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";

const sdkRoot = resolve(import.meta.dirname, "..");
const destination = resolve(sdkRoot, "../../.context-engine/sdk");
mkdirSync(destination, { recursive: true });

const result = spawnSync("npm", ["pack", "--pack-destination", destination], {
  cwd: sdkRoot,
  encoding: "utf8",
  stdio: "inherit",
  timeout: 120_000,
});
if (result.error !== undefined) {
  throw result.error;
}
if (result.status !== 0) {
  process.exit(result.status ?? 1);
}
