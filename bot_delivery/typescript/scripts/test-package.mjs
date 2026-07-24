import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { builtinModules } from "node:module";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";

import ts from "typescript";

const packageRoot = resolve(import.meta.dirname, "..");
const sdkRoot = resolve(packageRoot, "../../sdk/typescript");
const temporaryRoot = mkdtempSync(join(tmpdir(), "context-engine-bot-delivery-package-"));
const builtins = new Set(builtinModules.flatMap((name) => [name, `node:${name}`]));

function externalPackageName(specifier) {
  if (
    specifier.startsWith(".")
    || specifier.startsWith("/")
    || specifier.startsWith("#")
    || specifier.startsWith("node:")
    || builtins.has(specifier)
  ) {
    return undefined;
  }
  const segments = specifier.split("/");
  return specifier.startsWith("@") ? segments.slice(0, 2).join("/") : segments[0];
}

function importedPackages(filename) {
  const sourceFile = ts.createSourceFile(
    filename,
    readFileSync(filename, "utf8"),
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.JS,
  );
  const imported = new Set();
  function visit(node) {
    let moduleSpecifier;
    if (
      (ts.isImportDeclaration(node) || ts.isExportDeclaration(node))
      && node.moduleSpecifier !== undefined
      && ts.isStringLiteralLike(node.moduleSpecifier)
    ) {
      moduleSpecifier = node.moduleSpecifier.text;
    } else if (
      ts.isCallExpression(node)
      && (
        node.expression.kind === ts.SyntaxKind.ImportKeyword
        || (ts.isIdentifier(node.expression) && node.expression.text === "require")
      )
      && node.arguments.length === 1
      && ts.isStringLiteralLike(node.arguments[0])
    ) {
      moduleSpecifier = node.arguments[0].text;
    }
    if (moduleSpecifier !== undefined) {
      const packageName = externalPackageName(moduleSpecifier);
      if (packageName !== undefined) imported.add(packageName);
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return imported;
}

function assertPackedRuntimeImportsAreDeclared(installedPackageRoot, packedFiles) {
  const document = JSON.parse(readFileSync(join(installedPackageRoot, "package.json"), "utf8"));
  const declared = new Set([
    ...Object.keys(document.dependencies ?? {}),
    ...Object.keys(document.optionalDependencies ?? {}),
    ...Object.keys(document.peerDependencies ?? {}),
  ]);
  const observed = new Set();
  const undeclared = new Set();
  for (const packedFile of packedFiles) {
    if (!packedFile.path.endsWith(".js")) continue;
    for (const packageName of importedPackages(resolve(installedPackageRoot, packedFile.path))) {
      observed.add(packageName);
      if (!declared.has(packageName)) undeclared.add(packageName);
    }
  }
  if (undeclared.size > 0) {
    throw new Error(
      `packed BotDelivery has undeclared runtime imports: ${[...undeclared].sort().join(", ")}`,
    );
  }
  for (const required of ["canonicalize", "pg"]) {
    if (!observed.has(required)) {
      throw new Error(`packed runtime import audit did not observe ${required}`);
    }
  }
}

function run(command, args, cwd) {
  const result = spawnSync(command, args, {
    cwd,
    encoding: "utf8",
    env: {
      ...process.env,
      npm_config_cache: join(temporaryRoot, "npm-cache"),
      npm_config_offline: "true",
    },
    timeout: 120_000,
  });
  if (result.status !== 0) {
    throw new Error(
      `${command} ${args.join(" ")} failed:\n${result.error ?? ""}\n${result.stdout}\n${result.stderr}`,
    );
  }
  return result.stdout;
}

function pack(packageDirectory) {
  const output = run(
    "npm",
    ["pack", "--json", "--ignore-scripts", "--pack-destination", temporaryRoot],
    packageDirectory,
  );
  const report = JSON.parse(output);
  const filename = report[0]?.filename;
  if (typeof filename !== "string") {
    throw new Error(`npm pack did not report an artifact for ${packageDirectory}`);
  }
  return { filename, report: report[0] };
}

try {
  const botArtifact = pack(packageRoot);
  const sdkArtifact = pack(sdkRoot);
  const consumerRoot = join(temporaryRoot, "consumer");
  cpSync(resolve(packageRoot, "test/package-consumer"), consumerRoot, { recursive: true });
  const packageDocument = JSON.parse(readFileSync(join(consumerRoot, "package.json"), "utf8"));
  const packageLock = JSON.parse(readFileSync(join(packageRoot, "package-lock.json"), "utf8"));
  const localProductionDependencies = {};
  const localOptionalDependencies = {};
  for (const [dependencyPath, metadata] of Object.entries(packageLock.packages ?? {})) {
    if (!dependencyPath.startsWith("node_modules/") || metadata.dev === true) continue;
    const dependencyRoot = resolve(packageRoot, dependencyPath);
    const dependencyDocument = JSON.parse(readFileSync(join(dependencyRoot, "package.json"), "utf8"));
    const target = metadata.optional === true
      ? localOptionalDependencies
      : localProductionDependencies;
    target[dependencyDocument.name] = `file:${dependencyRoot}`;
  }
  packageDocument.dependencies = {
    "@context-engine/bot-delivery": `file:${join(temporaryRoot, botArtifact.filename)}`,
    "@context-engine/resolve-sdk": `file:${join(temporaryRoot, sdkArtifact.filename)}`,
    ...localProductionDependencies,
  };
  packageDocument.optionalDependencies = localOptionalDependencies;
  writeFileSync(join(consumerRoot, "package.json"), `${JSON.stringify(packageDocument, null, 2)}\n`);
  run("npm", ["install", "--ignore-scripts"], consumerRoot);
  assertPackedRuntimeImportsAreDeclared(
    join(consumerRoot, "node_modules/@context-engine/bot-delivery"),
    botArtifact.report?.files ?? [],
  );
  run(
    process.execPath,
    [resolve(packageRoot, "node_modules/typescript/bin/tsc"), "--project", "tsconfig.json"],
    consumerRoot,
  );
  run(process.execPath, ["runtime.mjs"], consumerRoot);
  process.stdout.write("packed BotDelivery consumer passed\n");
} finally {
  rmSync(temporaryRoot, { force: true, recursive: true });
}
