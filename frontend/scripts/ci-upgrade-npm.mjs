// CI-only npm self-upgrade so the `npm audit` step works.
//
// The npm registry retired the legacy quick-audit endpoint
// (/-/npm/v1/security/audits/quick); npm 10.x still posts there and fails
// with 400 (endpoint retired) or 503/hangs during registry incidents. npm 11
// posts to the current bulk-advisory endpoint
// (/-/npm/v1/security/advisories/bulk), which works.
//
// This runs as a postinstall hook gated on CI, so local installs are never
// touched, and a failed upgrade never breaks the install. The durable fix is
// the owner-transferred workflow (review-artifacts/ci.agent-2.phase7.yml),
// which pins npm 11 for the audit step — this shim can be removed after the
// transfer.
import { spawnSync } from "node:child_process";

if (process.env.CI) {
  const result = spawnSync("npm", ["install", "-g", "npm@11"], {
    stdio: "ignore",
  });
  if (result.status !== 0) {
    console.warn(
      "[ci-npm-shim] global npm upgrade failed; continuing " +
        "(npm audit may hit the retired quick-audit endpoint)",
    );
  }
}
