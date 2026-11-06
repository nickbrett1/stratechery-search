import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

// The suite lives in web/ but the thing under test is the pair: a Python server
// with an index, and the built frontend in web/dist. So the server process is
// started from the repository root, against the fixture corpus, and every test
// talks to it over HTTP exactly as a browser would.
const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..");

const port = Number(process.env.E2E_PORT || 8092);
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
  // One browser, one corpus. The suite is a smoke test for "does the app render
  // and answer", not a coverage instrument, so a failure should be the first
  // thing in the report rather than a retry.
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: process.env.CI ? [["list"], ["github"]] : [["list"]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: {
    // `scripts/e2e-corpus.py` is idempotent, so a local rerun does not rebuild
    // the index and CI never gets a stale one. The frontend is assumed built:
    // `npm run build` is the step before this one, locally and in CI.
    command:
      "python3 scripts/e2e-corpus.py .e2e && " +
      `PORT=${port} STATIC_DIR=web/dist INDEX_PATH=.e2e/index/search.sqlite ` +
      "ARCHIVE_ROOT=.e2e/archive PYTHONPATH=src python3 -m stratechery_search",
    cwd: repoRoot,
    url: `${baseURL}/stats`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
