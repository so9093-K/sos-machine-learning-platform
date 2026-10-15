import { expect, test } from "@playwright/test";

const viewer = process.env.SOS_E2E_VIEWER_TOKEN;
const researcher = process.env.SOS_E2E_RESEARCHER_TOKEN;
const operator = process.env.SOS_E2E_OPERATOR_TOKEN;

test.describe("token identity and resource permissions", () => {
  test.skip(!viewer || !researcher || !operator, "Requires isolated CI token-mode service");

  async function signIn(page: import("@playwright/test").Page, token: string) {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "SOS Platform 접근 인증" })).toBeVisible();
    await page.getByLabel("액세스 토큰").fill(token);
    await page.getByRole("button", { name: "인증", exact: true }).click();
  }

  test("viewer can read datasets but cannot upload or inspect storage", async ({ page }) => {
    await signIn(page, viewer!);
    await expect(page.locator(".auth-status")).toContainText("ci-viewer");
    await expect(page.getByRole("button", { name: "저장소 상태 보기" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "CSV 또는 Parquet 파일 업로드" }))
      .toHaveCount(0);
    await expect(page.getByRole("button", { name: "Iceberg에 추가" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "스냅샷 연결" })).toHaveCount(0);
    const response = await page.request.get("/api/storage/policy", {
      headers: { Authorization: `Bearer ${viewer}` },
    });
    expect(response.status()).toBe(403);
    await page.getByRole("button", { name: "인증 해제" }).click();
    await expect(page.getByRole("heading", { name: "SOS Platform 접근 인증" })).toBeVisible();
  });

  test("researcher can create datasets but cannot use operator tools", async ({ page }) => {
    await signIn(page, researcher!);
    await expect(page.getByRole("button", { name: "CSV 또는 Parquet 파일 업로드" }))
      .toBeVisible();
    await expect(page.getByRole("button", { name: "저장소 상태 보기" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "스냅샷 연결" })).toBeVisible();
    await page.locator('input[type="file"]').setInputFiles({
      name: "authorized.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("time,value\n1,10\n2,20\n"),
    });
    await expect(page.getByRole("heading", { name: "authorized.csv" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Iceberg에 추가" })).toBeVisible();
  });

  test("operator has diagnostic access but cannot read user datasets", async ({ page }) => {
    await signIn(page, operator!);
    await expect(page.getByRole("heading", { name: "Storage Health" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Security Audit" })).toBeVisible();
    const response = await page.request.get("/api/datasets", {
      headers: { Authorization: `Bearer ${operator}` },
    });
    expect(response.status()).toBe(403);
  });
});
