import { copyFileSync, cpSync, mkdirSync, rmSync, statSync } from "node:fs";
import { basename, resolve } from "node:path";
import { spawnSync } from "node:child_process";

const sdkRoot = resolve(import.meta.dirname, "..");
const repositoryRoot = resolve(sdkRoot, "../..");
const destination = resolve(sdkRoot, "../../.context-engine/sdk");
const governance = resolve(sdkRoot, "governance");
mkdirSync(destination, { recursive: true });
rmSync(governance, { recursive: true, force: true });
rmSync(resolve(sdkRoot, "third_party"), { recursive: true, force: true });
mkdirSync(governance, { recursive: true });
copyFileSync(
  resolve(repositoryRoot, "THIRD_PARTY_NOTICES.md"),
  resolve(governance, "THIRD_PARTY_NOTICES.md"),
);
copyFileSync(
  resolve(repositoryRoot, "THIRD_PARTY_SBOM.cyclonedx.json"),
  resolve(governance, "THIRD_PARTY_SBOM.cyclonedx.json"),
);
cpSync(resolve(repositoryRoot, "third_party"), resolve(sdkRoot, "third_party"), {
  recursive: true,
  filter: (source) => statSync(source).isDirectory() || basename(source).startsWith("LICENSE"),
});

const result = spawnSync("npm", ["pack", "--pack-destination", destination], {
  cwd: sdkRoot,
  encoding: "utf8",
  env: {
    ...process.env,
    npm_config_cache: resolve(repositoryRoot, ".context-engine/npm-cache"),
  },
  stdio: "inherit",
  timeout: 120_000,
});
if (result.error !== undefined) {
  throw result.error;
}
if (result.status !== 0) {
  process.exit(result.status ?? 1);
}
