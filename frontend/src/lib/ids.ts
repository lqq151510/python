/**
 * Request identifiers for chat turns.
 *
 * `crypto.randomUUID` only exists in a secure context (HTTPS or localhost). When
 * the app is served over plain HTTP — a LAN address, a container hostname such as
 * `http://backend:8000`, or an internal hostname — the function is undefined and
 * calling it throws, which used to break "send question" before any request left
 * the browser. `crypto.getRandomValues` has no such restriction, so the fallback
 * builds the same RFC 4122 version 4 UUID from random bytes.
 */

const HEX_BYTES = 16

function randomBytes(): Uint8Array {
  const bytes = new Uint8Array(HEX_BYTES)
  const source = typeof crypto === "undefined" ? undefined : crypto
  if (source && typeof source.getRandomValues === "function") {
    source.getRandomValues(bytes)
    return bytes
  }
  // Last resort: only reached on platforms without any Web Crypto at all.
  for (let index = 0; index < bytes.length; index += 1) {
    bytes[index] = Math.floor(Math.random() * 256)
  }
  return bytes
}

function formatUuidV4(bytes: Uint8Array): string {
  // Set the version (4) and variant (10xx) bits required by RFC 4122.
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("")
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-")
}

/** Return a version 4 UUID, on secure and insecure origins alike. */
export function createRequestId(): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID()
  }
  return formatUuidV4(randomBytes())
}
