import { createHash } from "node:crypto";
import {
  cpSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, join, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";

const sdkRoot = resolve(import.meta.dirname, "..");
const repositoryRoot = resolve(sdkRoot, "../..");
const snapshotPath = resolve(repositoryRoot, "openapi/v0/openapi.json");
const snapshotDigestPath = resolve(repositoryRoot, "openapi/v0/openapi.sha256");
const generatedPath = resolve(sdkRoot, "src/generated");
const generatedDigestPath = resolve(sdkRoot, "src/generated.sha256");
const contractDigestPath = resolve(sdkRoot, "contract/openapi-v0.sha256");
const checkOnly = process.argv.slice(2).includes("--check");

const expectedSnapshotDigest = readFileSync(snapshotDigestPath, "ascii").trim();
const observedSnapshotDigest = createHash("sha256")
  .update(readFileSync(snapshotPath))
  .digest("hex");
if (observedSnapshotDigest !== expectedSnapshotDigest) {
  throw new Error("OpenAPI v0 snapshot does not match its accepted checksum");
}

function filesBelow(root) {
  const pending = [root];
  const files = [];
  while (pending.length > 0) {
    const current = pending.pop();
    if (current === undefined) {
      continue;
    }
    for (const name of readdirSync(current).sort().reverse()) {
      const path = join(current, name);
      if (statSync(path).isDirectory()) {
        pending.push(path);
      } else {
        files.push(path);
      }
    }
  }
  return files.sort();
}

function generatedTree(root) {
  return new Map(
    filesBelow(root).map((path) => [relative(root, path), readFileSync(path)]),
  );
}

function treeDigest(tree) {
  const digest = createHash("sha256");
  digest.update("context-engine-generated-sdk-v1\0");
  for (const [path, contents] of tree) {
    digest.update(path);
    digest.update("\0");
    digest.update(String(contents.byteLength));
    digest.update("\0");
    digest.update(contents);
  }
  return digest.digest("hex");
}

const temporaryRoot = mkdtempSync(join(tmpdir(), "context-engine-sdk-generate-"));
const temporaryOutput = join(temporaryRoot, "generated");
try {
  const executable = resolve(
    sdkRoot,
    `node_modules/.bin/openapi-ts${process.platform === "win32" ? ".cmd" : ""}`,
  );
  const generation = spawnSync(
    executable,
    [
      "--input",
      snapshotPath,
      "--output",
      temporaryOutput,
      "--client",
      "@hey-api/client-fetch",
      "--silent",
      "--no-log-file",
    ],
    { cwd: sdkRoot, encoding: "utf8", timeout: 120_000 },
  );
  if (generation.status !== 0) {
    throw new Error(
      `SDK generation failed: ${generation.error ?? generation.stderr ?? generation.stdout}`,
    );
  }

  const candidate = generatedTree(temporaryOutput);
  const candidateDigest = treeDigest(candidate);
  if (checkOnly) {
    const tracked = generatedTree(generatedPath);
    const differences = new Set([...candidate.keys(), ...tracked.keys()]);
    for (const path of differences) {
      if (!candidate.get(path)?.equals(tracked.get(path))) {
        throw new Error(`generated SDK drifted at ${path}`);
      }
    }
    const trackedDigest = readFileSync(generatedDigestPath, "ascii").trim();
    if (trackedDigest !== candidateDigest) {
      throw new Error("generated SDK checksum drifted");
    }
    const packagedContractDigest = readFileSync(contractDigestPath, "ascii").trim();
    if (packagedContractDigest !== expectedSnapshotDigest) {
      throw new Error("packaged OpenAPI v0 provenance checksum drifted");
    }
    process.stdout.write(`generated SDK clean: ${candidateDigest}\n`);
  } else {
    rmSync(generatedPath, { force: true, recursive: true });
    cpSync(temporaryOutput, generatedPath, { recursive: true });
    writeFileSync(generatedDigestPath, `${candidateDigest}\n`, "ascii");
    mkdirSync(resolve(contractDigestPath, ".."), { recursive: true });
    writeFileSync(contractDigestPath, `${expectedSnapshotDigest}\n`, "ascii");
    process.stdout.write(
      `generated ${candidate.size} files in ${basename(generatedPath)}: ${candidateDigest}\n`,
    );
  }
} finally {
  rmSync(temporaryRoot, { force: true, recursive: true });
}
