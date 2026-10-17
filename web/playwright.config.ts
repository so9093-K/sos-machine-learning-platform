import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  retries: 0,
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${process.env.SOS_WEB_PORT || 9601}`,
    browserName: "chromium",
    trace: "retain-on-failure",
  },
  reporter: process.env.CI || process.env.SOS_JUNIT_FILE
    ? [["list"], ["junit", { outputFile: process.env.SOS_JUNIT_FILE || "reports/default.xml" }]]
    : "list",
});
