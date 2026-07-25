/**
 * Identifier generation. Used for conversation ids, which double as the backend
 * session id, and for message ids.
 */

/**
 * Generate an identifier. crypto.randomUUID is used when available, with a
 * small fallback for older or non secure contexts where it is missing.
 */
export function createId(): string {
  const cryptoRef: Crypto | undefined =
    typeof globalThis.crypto !== 'undefined' ? globalThis.crypto : undefined;

  if (cryptoRef && typeof cryptoRef.randomUUID === 'function') {
    return cryptoRef.randomUUID();
  }

  if (cryptoRef && typeof cryptoRef.getRandomValues === 'function') {
    const bytes = new Uint8Array(16);
    cryptoRef.getRandomValues(bytes);
    // Set the version and variant bits so the shape is a valid v4 UUID.
    bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
    bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
    const hex: string[] = [];
    for (let i = 0; i < bytes.length; i += 1) {
      hex.push((bytes[i] ?? 0).toString(16).padStart(2, '0'));
    }
    return [
      hex.slice(0, 4).join(''),
      hex.slice(4, 6).join(''),
      hex.slice(6, 8).join(''),
      hex.slice(8, 10).join(''),
      hex.slice(10, 16).join(''),
    ].join('-');
  }

  return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}
