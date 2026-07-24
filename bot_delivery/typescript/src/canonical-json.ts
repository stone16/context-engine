import { createHash } from "node:crypto";

import canonicalize from "canonicalize";

function requireUnicodeScalars(value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) {
        throw new TypeError("canonical JSON strings must contain Unicode scalar values");
      }
      index += 1;
    } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
      throw new TypeError("canonical JSON strings must contain Unicode scalar values");
    }
  }
}

function validateCanonicalJson(value: unknown, ancestors: Set<object>): void {
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "string") {
    requireUnicodeScalars(value);
    return;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new TypeError("canonical JSON numbers must be finite IEEE 754 values");
    }
    return;
  }
  if (typeof value !== "object") {
    throw new TypeError("canonical JSON accepts only exact JSON values");
  }
  if (ancestors.has(value)) {
    throw new TypeError("canonical JSON must not contain cyclic containers");
  }
  ancestors.add(value);
  try {
    if (Array.isArray(value)) {
      if (Object.getPrototypeOf(value) !== Array.prototype) {
        throw new TypeError("canonical JSON accepts only exact arrays");
      }
      const ownKeys = Reflect.ownKeys(value);
      const expectedKeys = Array.from({ length: value.length }, (_, index) => String(index));
      if (
        ownKeys.length !== expectedKeys.length + 1
        || ownKeys[ownKeys.length - 1] !== "length"
        || expectedKeys.some((key, index) => ownKeys[index] !== key)
      ) {
        throw new TypeError("canonical JSON arrays must be dense and unadorned");
      }
      for (const item of value) validateCanonicalJson(item, ancestors);
      return;
    }
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) {
      throw new TypeError("canonical JSON accepts only exact objects");
    }
    const ownKeys = Reflect.ownKeys(value);
    if (ownKeys.some((key) => typeof key !== "string")) {
      throw new TypeError("canonical JSON objects require exact string keys");
    }
    for (const key of ownKeys as string[]) {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (descriptor?.enumerable !== true || !("value" in descriptor)) {
        throw new TypeError("canonical JSON objects require enumerable data properties");
      }
      requireUnicodeScalars(key);
      validateCanonicalJson(descriptor.value, ancestors);
    }
  } finally {
    ancestors.delete(value);
  }
}

export function canonicalJson(value: unknown): Buffer {
  validateCanonicalJson(value, new Set());
  const encoded = canonicalize(value);
  if (encoded === undefined) {
    throw new TypeError("value is outside the RFC 8785 JSON domain");
  }
  return Buffer.from(encoded, "utf8");
}

function rejectEmbeddedPackageDigest(value: unknown): void {
  if (Array.isArray(value)) {
    for (const item of value) rejectEmbeddedPackageDigest(item);
    return;
  }
  if (typeof value !== "object" || value === null) return;
  for (const key of Object.keys(value)) {
    if (key === "packageDigest") {
      throw new TypeError("Package digest document must not contain packageDigest");
    }
    rejectEmbeddedPackageDigest((value as Readonly<Record<string, unknown>>)[key]);
  }
}

/** Digest one Package document whose wire-level digest field has been excluded. */
export function contextPackageDocumentDigest(value: unknown): string {
  rejectEmbeddedPackageDigest(value);
  return createHash("sha256").update(canonicalJson(value)).digest("hex");
}

/** Internal conformance seam; package exports do not expose this module. */
export function canonicalJsonDigest(value: unknown): string {
  return createHash("sha256").update(canonicalJson(value)).digest("hex");
}
