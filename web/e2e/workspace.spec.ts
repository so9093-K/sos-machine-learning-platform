import { expect, test, type Page } from "@playwright/test";

async function submittedJob(page: Page, route: string, click: () => Promise<void>): Promise<string> {
  const [response] = await Promise.all([
    page.waitForResponse(res => res.url().includes(route) && res.request().method() === "POST"),
    click(),
  ]);
  expect(response.ok()).toBeTruthy();
  const job = (await response.json()) as { id: string };
  expect(job.id).toMatch(/^[0-9a-f-]{36}$/i);
  return job.id;
}

function jobById(page: Page, id: string) {
  return page.locator(".job").filter({ has: page.locator(".job-title small", { hasText: id }) });
}

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
  await expect(page.getByText("변환 미리보기 · 전체 입력 기준 계산")).toBeVisible();
  const jobId = await submittedJob(page, "/api/jobs", () =>
    page.getByRole("button", { name: /전체 변환 Job 실행/ }).click());
  const latestTransform = jobById(page, jobId);
  await expect(latestTransform.locator(".status")).toContainText("Succeeded", { timeout: 30000 });
  await expect(latestTransform.getByText(/입력 데이터/)).toBeVisible();
  await latestTransform.getByText(/작업 설정 및 실행 시도/).click();
  await expect(latestTransform.getByText(/Succeeded ·/)).toBeVisible();
  await expect(page.locator("nav").getByText(/features-/).first()).toBeVisible();
});

test("Iceberg import captures a pinned snapshot", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "repeat.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("time,value\n1,5\n2,6\n"),
  });
  await expect(page.getByRole("heading", { name: "repeat.csv" })).toBeVisible();
  const tableName = `sensor_${Date.now()}_${Math.floor(Math.random() * 1_000_000)}`;
  await page.getByRole("textbox", { name: "Iceberg 테이블명" }).fill(tableName);
  const jobId = await submittedJob(page, "/api/iceberg/import", () =>
    page.getByRole("button", { name: "Iceberg에 추가" }).click());
  const latestImport = jobById(page, jobId);
  await expect(latestImport.locator(".status")).toContainText("Succeeded", { timeout: 30000 });
  await expect(page.locator("nav").getByText(`default.${tableName}`)).toBeVisible();
  await page.locator("nav").getByText(`default.${tableName}`).click();
  await expect(page.locator(".dataset-hero p")).toContainText("Snapshot", { timeout: 30000 });
  await page.getByRole("tab", { name: "Preview" }).click();
  await expect(page.getByText("2 rows shown")).toBeVisible();
});


test("example onboarding uses the same EDA, Feature and Job contracts", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "예제 데이터 살펴보기" }).click();
  await expect(page.getByText("예제 데이터로 먼저 살펴보기")).toBeVisible();
  await expect(page.getByText("합성 데이터입니다. 선택할 때만 일반 데이터셋으로 등록됩니다.")).toBeVisible();
  await page.getByRole("button", { name: /이 예제로 탐색 시작/ }).first().click();
  await expect(page.getByRole("heading", { name: "example-machine-sensors.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "EDA & Profile" }).click();
  await expect(page.getByText("분석한 행")).toBeVisible();
  await page.getByRole("tab", { name: "Features" }).click();
  await page.getByLabel("변환", { exact: true }).selectOption("lag");
  await page.getByLabel("컬럼", { exact: true }).selectOption("temperature");
  await page.getByLabel("시간·정렬 컬럼").selectOption("time");
  await page.getByLabel("그룹 컬럼").selectOption("machine_id");
  await page.getByRole("button", { name: "+ 단계 추가" }).click();
  await page.getByRole("button", { name: "샘플 변환 확인" }).click();
  await expect(page.getByText("변환 미리보기 · 전체 입력 기준 계산")).toBeVisible();
  const jobId = await submittedJob(page, "/api/jobs", () =>
    page.getByRole("button", { name: /전체 변환 Job 실행/ }).click());
  const job = jobById(page, jobId);
  await expect(job.locator(".status")).toContainText("Succeeded", { timeout: 30000 });
  await job.getByRole("button", { name: /결과 데이터 열기/ }).click();
  await expect(page.getByRole("heading", { name: /features-/ })).toBeVisible();
});

test("workspace tabs support keyboard navigation", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "예제 데이터 살펴보기" }).click();
  await page.getByRole("button", { name: /이 예제로 탐색 시작/ }).first().click();
  const preview = page.getByRole("tab", { name: "Preview" });
  await preview.focus();
  await preview.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "EDA & Profile" })).toHaveAttribute(
    "aria-selected", "true");
  await expect(page.getByRole("tabpanel", { name: "EDA & Profile" })).toBeVisible();
});


test("feature preview is invalidated when group or ordering changes", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "예제 데이터 살펴보기" }).click();
  await page.getByRole("button", { name: /이 예제로 탐색 시작/ }).first().click();
  await page.getByRole("tab", { name: "Features" }).click();
  await page.getByLabel("변환", { exact: true }).selectOption("lag");
  await page.getByLabel("컬럼", { exact: true }).selectOption("temperature");
  await page.getByLabel("시간·정렬 컬럼").selectOption("time");
  await page.getByLabel("그룹 컬럼").selectOption("machine_id");
  await page.getByRole("button", { name: "+ 단계 추가" }).click();
  await page.getByRole("button", { name: "샘플 변환 확인" }).click();
  await expect(page.getByText("변환 미리보기 · 전체 입력 기준 계산")).toBeVisible();
  await page.getByLabel("그룹 컬럼").selectOption("");
  await expect(page.getByText("변환 미리보기 · 전체 입력 기준 계산")).toHaveCount(0);
  await page.getByRole("button", { name: "샘플 변환 확인" }).click();
  await expect(page.getByRole("alert")).toContainText("unique within each group");
  await page.getByLabel("그룹 컬럼").selectOption("machine_id");
  await page.getByRole("button", { name: "샘플 변환 확인" }).click();
  await expect(page.getByText("변환 미리보기 · 전체 입력 기준 계산")).toBeVisible();
});


test("file upload is operable through keyboard", async ({ page }) => {
  await page.goto("/");
  const upload = page.getByRole("button", { name: "CSV 또는 Parquet 파일 업로드" });
  await upload.focus();
  const chooser = page.waitForEvent("filechooser");
  await upload.press("Enter");
  const fileChooser = await chooser;
  await fileChooser.setFiles({
    name: "keyboard.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("time,value\n1,10\n2,20\n"),
  });
  await expect(page.getByRole("heading", { name: "keyboard.csv" })).toBeVisible();
});


test("same dataset reload preserves Feature editor selections", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "예제 데이터 살펴보기" }).click();
  await page.getByRole("button", { name: /이 예제로 탐색 시작/ }).first().click();
  await page.getByRole("tab", { name: "Features" }).click();
  await page.getByLabel("변환", { exact: true }).selectOption("lag");
  await page.getByLabel("컬럼", { exact: true }).selectOption("temperature");
  await page.getByLabel("시간·정렬 컬럼").selectOption("time");
  await page.getByLabel("그룹 컬럼").selectOption("machine_id");
  await page.getByRole("button", { name: "+ 단계 추가" }).click();
  await page.getByLabel("컬럼", { exact: true }).selectOption("vibration");
  await page.getByRole("button", { name: "데이터 다시 불러오기" }).click();
  await expect(page.getByLabel("컬럼", { exact: true })).toHaveValue("vibration");
  await expect(page.getByLabel("시간·정렬 컬럼")).toHaveValue("time");
  await expect(page.getByLabel("그룹 컬럼")).toHaveValue("machine_id");
  await expect(page.locator(".step")).toHaveCount(1);
  await page.getByRole("button", { name: "샘플 변환 확인" }).click();
  await expect(page.getByText("변환 미리보기 · 전체 입력 기준 계산")).toBeVisible();
});


test("Storage Health presents effective safe policy without destructive actions", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "저장소 상태 보기" }).click();
  const storage = page.getByRole("region", { name: "저장소 상태" });
  await expect(storage.getByRole("heading", { name: "Storage Health" })).toBeVisible();
  await expect(storage.getByText("비활성화", { exact: true })).toBeVisible();
  await expect(storage.getByText("적용 중인 보존 정책")).toBeVisible();
  await expect(storage.getByRole("button", { name: "객체 다시 검사" })).toBeVisible();
  await storage.getByRole("button", { name: "Snapshot 검사" }).click();
  await expect(storage.getByText("등록된 Iceberg 참조가 없습니다.").or(
    storage.getByText(/고정 Snapshot과 만료 기한 없는 SOS 보호 Tag|Snapshot은 존재하지만 유효한 보존 Tag|현재 읽을 수 있지만 SOS 보호 Tag|Catalog 확인이 불가능합니다/)
  ).first()).toBeVisible();
  await expect(storage.getByRole("button", { name: /삭제 실행/ })).toHaveCount(0);
  await expect(storage.getByText(/마지막 성공 확인:/)).toBeVisible();
  await expect(storage.getByText(/검토 후보는 삭제 대상이 아닙니다/)).toBeVisible();
  await page.route("**/api/storage/inspection*", route => route.fulfill({
    status: 503, contentType: "application/json",
    body: JSON.stringify({ detail: "inventory temporarily unavailable" }),
  }));
  await storage.getByRole("button", { name: "객체 다시 검사" }).click();
  await expect(storage.getByRole("alert")).toContainText("현재 상태가 아닙니다");
});
