import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const sdkRoot = resolve(import.meta.dirname, "..");
const requiredNode = readFileSync(resolve(sdkRoot, ".node-version"), "ascii").trim();
const observedNode = process.versions.node;
if (observedNode !== requiredNode) {
  throw new Error(
    `SDK requires exact Node ${requiredNode}; observed ${observedNode}`,
  );
}

const packageDocument = JSON.parse(
  readFileSync(resolve(sdkRoot, "package.json"), "utf8"),
);
const requiredPackageManager = packageDocument.packageManager;
const observedPackageManager = process.env.npm_config_user_agent
  ?.split(" ")[0]
  ?.replace("/", "@");
if (observedPackageManager !== requiredPackageManager) {
  throw new Error(
    `SDK requires exact ${requiredPackageManager}; observed ${observedPackageManager ?? "unknown"}`,
  );
}
