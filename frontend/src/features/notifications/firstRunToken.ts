/** Extracts the one-shot first-run exchange token from the location hash.

 * The installer opens `/#first-run?t=<token>`; the token stays out of the
 * history and is passed to the backend exactly once.
 */
const TOKEN_PATTERN = /[#&?]t=([A-Za-z0-9_-]+)/;

export function tokenFromHash(hash: string = window.location.hash): string | null {
  const match = hash.match(TOKEN_PATTERN);
  return match ? match[1] : null;
}
