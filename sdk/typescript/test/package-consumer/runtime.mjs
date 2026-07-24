import { readFile } from "node:fs/promises";

const module = await import("@context-engine/resolve-sdk");
if (typeof module.ContextEngineResolveClient !== "function") {
  throw new Error("packed consumer did not load the public SDK facade");
}
const checksumUrl = import.meta.resolve(
  "@context-engine/resolve-sdk/contract/openapi-v0.sha256",
);
const checksum = (await readFile(new URL(checksumUrl), "ascii")).trim();
if (!/^[0-9a-f]{64}$/.test(checksum)) {
  throw new Error("packed package lost versioned contract provenance");
}
