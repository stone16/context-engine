import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";

const sdkRoot = resolve(import.meta.dirname, "..");
const temporaryRoot = mkdtempSync(join(tmpdir(), "context-engine-sdk-package-"));

function run(command, args, cwd) {
  const result = spawnSync(command, args, {
    cwd,
    encoding: "utf8",
    env: { ...process.env, npm_config_offline: "true" },
    timeout: 120_000,
  });
  if (result.status !== 0) {
    throw new Error(
      `${command} ${args.join(" ")} failed:\n${result.error ?? ""}\n${result.stdout}\n${result.stderr}`,
    );
  }
  return result.stdout;
}

try {
  const packOutput = run(
    "npm",
    ["pack", "--json", "--ignore-scripts", "--pack-destination", temporaryRoot],
    sdkRoot,
  );
  const packReport = JSON.parse(packOutput);
  const filename = packReport[0]?.filename;
  if (typeof filename !== "string") {
    throw new Error("npm pack did not report an artifact filename");
  }
  const consumerRoot = join(temporaryRoot, "consumer");
  cpSync(resolve(sdkRoot, "test/package-consumer"), consumerRoot, {
    recursive: true,
  });
  const packageDocument = JSON.parse(
    readFileSync(join(consumerRoot, "package.json"), "utf8"),
  );
  packageDocument.dependencies = {
    "@context-engine/resolve-sdk": `file:${join(temporaryRoot, filename)}`,
  };
  writeFileSync(
    join(consumerRoot, "package.json"),
    `${JSON.stringify(packageDocument, null, 2)}\n`,
  );
  run("npm", ["install", "--ignore-scripts"], consumerRoot);
  run(
    process.execPath,
    [resolve(sdkRoot, "node_modules/typescript/bin/tsc"), "--project", "tsconfig.json"],
    consumerRoot,
  );
  run(process.execPath, ["runtime.mjs"], consumerRoot);
  process.stdout.write("packed SDK consumer passed\n");
} finally {
  rmSync(temporaryRoot, { force: true, recursive: true });
}
