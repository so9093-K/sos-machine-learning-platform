import { expect, test } from "@playwright/test";

test("CSV upload → EDA → feature preview → worker → reusable dataset", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "sensors.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("time,temperature\n1,22\n2,24\n3,25\n"),
  });
  await expect(page.getByRole("heading", { name: "sensors.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "EDA & Profile" }).click();
  await expect(page.getByText("분석한 행")).toBeVisible();
  await expect(page.getByText("Histogram")).toBeVisible();
  await page.getByRole("tab", { name: "Features" }).click();
  await page.getByRole("button", { name: "+ 단계 추가" }).click();
  await page.getByRole("button", { name: "샘플 변환 확인" }).click();
  await expect(page.getByText("변환 미리보기 · 샘플 기반")).toBeVisible();
  await page.getByRole("button", { name: /전체 변환 Job 실행/ }).click();
  await expect(page.getByText("Succeeded")).toBeVisible({ timeout: 30000 });
  await expect(page.locator("nav").getByText(/features-/)).toBeVisible();
});

test("Iceberg import captures a pinned snapshot", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "repeat.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("time,value\n1,5\n2,6\n"),
  });
  await expect(page.getByRole("heading", { name: "repeat.csv" })).toBeVisible();
  await page.getByRole("button", { name: "Iceberg에 추가" }).click();
  await expect(page.getByText("Iceberg Import")).toBeVisible();
  await expect(page.getByText("Succeeded")).toBeVisible({ timeout: 30000 });
  await expect(page.locator("nav").getByText("default.sensor_data")).toBeVisible();
  await page.locator("nav").getByText("default.sensor_data").click();
  await expect(page.locator(".dataset-hero p")).toContainText("Snapshot", { timeout: 30000 });
  await page.getByRole("tab", { name: "Preview" }).click();
  await expect(page.getByText("2 rows shown")).toBeVisible();
});
