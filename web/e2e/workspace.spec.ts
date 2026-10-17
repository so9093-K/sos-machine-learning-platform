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
  await page.getByRole("tab", { name: "탐색" }).click();
  await expect(page.getByText("분석한 행")).toBeVisible();
  await expect(page.getByText("Histogram")).toBeVisible();
  await page.getByRole("tab", { name: "변환" }).click();
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

test("an expanded Attempt detail catches up with a newer completed Job", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "detail-refresh.csv", mimeType: "text/csv", buffer: Buffer.from("value\n1\n2\n"),
  });
  await expect(page.getByRole("heading", { name: "detail-refresh.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "탐색" }).click();
  const jobId = await submittedJob(page, "/api/jobs", () =>
    page.getByRole("button", { name: /전체 Profile 실행/ }).click());
  const job = jobById(page, jobId);
  await expect(job.locator(".status")).toContainText("Succeeded", { timeout: 30000 });
  let detailRequests = 0;
  await page.route(`**/api/jobs/${jobId}`, async route => {
    const response = await route.fetch();
    const original = await response.json() as {
      status: string; updated_at: string; attempts: Array<{ status: string }>;
    };
    detailRequests += 1;
    await route.fulfill({ response, json: detailRequests === 1
      ? { ...original, status: "Running", updated_at: "2000-01-01T00:00:00+00:00",
          attempts: original.attempts.map(attempt => ({ ...attempt, status: "Running" })) }
      : original,
    });
  });
  await job.getByText(/작업 설정 및 실행 시도/).click();
  await expect(job.getByText(/Succeeded ·/)).toBeVisible({ timeout: 10000 });
  expect(detailRequests).toBeGreaterThanOrEqual(2);
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
  await page.getByRole("tab", { name: "데이터 조회" }).click();
  await expect(page.getByText("2 rows shown")).toBeVisible();
  await page.getByRole("textbox", { name: "Iceberg 테이블 이름" }).fill(tableName);
  await page.getByRole("button", { name: "Snapshot 조회" }).click();
  const selector = page.getByRole("combobox", { name: "Iceberg Snapshot 선택" });
  await expect(selector).toBeVisible();
  const snapshotId = await selector.inputValue();
  expect(snapshotId).toMatch(/^\d+$/);
  await page.getByRole("button", { name: "선택한 Snapshot 연결" }).click();
  await expect(page.locator(".dataset-hero p")).toContainText(snapshotId);
});


test("example onboarding uses the same EDA, Feature and Job contracts", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "예제 데이터 살펴보기" }).click();
  await expect(page.getByText("예제 데이터로 먼저 살펴보기")).toBeVisible();
  await expect(page.getByText("합성 데이터입니다. 선택할 때만 일반 데이터셋으로 등록됩니다.")).toBeVisible();
  await page.getByRole("button", { name: /이 예제로 탐색 시작/ }).first().click();
  await expect(page.getByRole("heading", { name: "example-machine-sensors.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "탐색" }).click();
  await expect(page.getByText("분석한 행")).toBeVisible();
  await page.getByRole("tab", { name: "변환" }).click();
  await page.getByLabel("변환 연산", { exact: true }).selectOption("lag");
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
  const preview = page.getByRole("tab", { name: "데이터 조회" });
  await preview.focus();
  await preview.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "탐색" })).toHaveAttribute(
    "aria-selected", "true");
  await expect(page.getByRole("tabpanel", { name: "탐색" })).toBeVisible();
});


test("feature preview is invalidated when group or ordering changes", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "예제 데이터 살펴보기" }).click();
  await page.getByRole("button", { name: /이 예제로 탐색 시작/ }).first().click();
  await page.getByRole("tab", { name: "변환" }).click();
  await page.getByLabel("변환 연산", { exact: true }).selectOption("lag");
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
  await page.getByRole("tab", { name: "변환" }).click();
  await page.getByLabel("변환 연산", { exact: true }).selectOption("lag");
  await page.getByLabel("컬럼", { exact: true }).selectOption("temperature");
  await page.getByLabel("시간·정렬 컬럼").selectOption("time");
  await page.getByLabel("그룹 컬럼").selectOption("machine_id");
  await page.getByRole("button", { name: "+ 단계 추가" }).click();
  await page.getByLabel("컬럼", { exact: true }).selectOption("vibration");
  await page.getByRole("tab", { name: "개요" }).click();
  await page.getByRole("tab", { name: "변환" }).click();
  await expect(page.getByLabel("컬럼", { exact: true })).toHaveValue("vibration");
  await expect(page.locator(".step")).toHaveCount(1);
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


test("file size is explained and rejected before upload", async ({ page }) => {
  await page.route("**/api/datasets/upload-policy", route =>
    route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ max_size_bytes: 1024 * 1024,
        supported_extensions: [".csv", ".parquet"] }),
    }));
  let uploads = 0;
  await page.route("**/api/datasets/files", route => {
    uploads++;
    return route.abort();
  });
  await page.goto("/");
  await expect(page.getByText("최대 1.00 MiB")).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles({
    name: "over-limit.csv",
    mimeType: "text/csv",
    buffer: Buffer.alloc(1024 * 1024 + 1, 120),
  });
  await expect(page.getByRole("alert")).toContainText("최대 1.00 MiB");
  expect(uploads).toBe(0);
});


test("201 registration remains successful when the subsequent list refresh fails", async ({ page }) => {
  let failRefresh = false;
  await page.route("**/api/datasets?*", route => {
    if (route.request().method() === "GET" && failRefresh) {
      return route.fulfill({ status: 503, contentType: "application/json",
        body: JSON.stringify({ detail: "List temporarily unavailable" }) });
    }
    return route.continue();
  });
  await page.route("**/api/datasets/files", async route => {
    const response = await route.fetch();
    expect(response.status()).toBe(201);
    failRefresh = true;
    await route.fulfill({ response });
  });
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "published.csv", mimeType: "text/csv",
    buffer: Buffer.from("signal\n1\n2\n"),
  });
  await expect(page.getByRole("heading", { name: "published.csv" })).toBeVisible();
  await expect(page.getByRole("status").filter({
    hasText: "파일은 정상 등록됐습니다.",
  })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("upload indicates pending transfer and completes after server response", async ({ page }) => {
  let releaseResponse: () => void = () => {};
  const heldResponse = new Promise<void>(resolve => { releaseResponse = resolve; });
  await page.route("**/api/datasets/files", async route => {
    const response = await route.fetch();
    await heldResponse;
    await route.fulfill({ response });
  });
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "progress.csv", mimeType: "text/csv",
    buffer: Buffer.from("time,value\n1,2\n2,3\n"),
  });
  try {
    // 네트워크 가로채기는 브라우저 전송 완료 이벤트 자체를 보류한다.
    // 전송 완료 콜백과 등록 대기 상태는 upload.test.ts에서 별도로 검증한다.
    await expect(page.getByText(/전송 중 ·/)).toBeVisible();
  } finally {
    releaseResponse();
  }
  await expect(page.getByRole("heading", { name: "progress.csv" })).toBeVisible();
  await expect(page.getByText("파일 등록이 완료됐습니다.", { exact: false })).toBeVisible();
});

test("DuckDB Discovery searches columns and runs a full job through global Jobs", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "discovery.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("group,value\na,1\na,3\nb,5\n"),
  });
  await expect(page.getByRole("heading", { name: "discovery.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "탐색" }).click();
  const discovery = page.getByRole("region", { name: "Dataset Discovery" });
  await expect(discovery.getByRole("heading", { name: "탐색 분석" })).toBeVisible();
  await expect(discovery.getByText(/분석 대상 · 2컬럼/)).toBeVisible();
  await expect(discovery.getByText(/\$\{overview\.column_count/)).toHaveCount(0);
  await discovery.getByText("컬럼 목록 및 검색").click();
  await expect(discovery.getByText("2개 표시 / 전체 2개 컬럼")).toBeVisible();
  await discovery.getByRole("textbox", { name: "컬럼 이름 검색" }).fill("val");
  await expect(discovery.getByText("1개 표시 / 전체 2개 컬럼")).toBeVisible();
  await discovery.getByRole("combobox", { name: "집계 그룹" }).selectOption("group");
  await discovery.getByRole("button", { name: "샘플 집계" }).click();
  await expect(discovery.getByText(/샘플 최대/)).toBeVisible();
  await expect(discovery.getByRole("table").getByText("2", { exact: true })).toBeVisible();
  const jobId = await submittedJob(page, "/api/explorations/jobs", () =>
    discovery.getByRole("button", { name: "전체 집계 Job 실행" }).click());
  const job = jobById(page, jobId);
  await expect(job.getByText("Full Discovery Analysis")).toBeVisible();
  await expect(job.getByRole("region", { name: "전체 집계 결과" })).toBeVisible();
  await expect(job.getByRole("button", { name: "결과 데이터 열기" })).toHaveCount(0);
  await expect(job.locator(".status")).toContainText("Succeeded", { timeout: 30000 });
  await job.getByText(/작업 설정 및 실행 시도/).click();
  await expect(job.getByText(/Succeeded ·/)).toBeVisible();
});


test("Discovery metadata retry restores selection and analysis controls", async ({ page }) => {
  let shouldFail = true;
  await page.route("**/api/datasets/*/columns", async route => {
    // StrictMode는 초기 Effect를 재실행하므로 재시도 클릭 전의 모든 호출을 실패시킨다.
    if (shouldFail) {
      await route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"일시적인 오류"}' });
    } else {
      await route.continue();
    }
  });
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "recovery.csv", mimeType: "text/csv", buffer: Buffer.from("group,value\na,2\nb,4\n"),
  });
  await expect(page.getByRole("heading", { name: "recovery.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "탐색" }).click();
  const discovery = page.getByRole("region", { name: "Dataset Discovery" });
  await expect(discovery.getByRole("button", { name: "메타데이터 다시 시도" })).toBeVisible();
  shouldFail = false;
  await discovery.getByRole("button", { name: "메타데이터 다시 시도" }).click();
  await expect(discovery.getByRole("button", { name: "샘플 집계" })).toBeEnabled();
  await discovery.getByRole("button", { name: "샘플 집계" }).click();
  await expect(discovery.getByText(/샘플 최대/)).toBeVisible();
});

test("Discovery supports categorical-only counts", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "labels.csv", mimeType: "text/csv",
    buffer: Buffer.from("group,label\na,x\na,\nb,y\n"),
  });
  await expect(page.getByRole("heading", { name: "labels.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "탐색" }).click();
  const discovery = page.getByRole("region", { name: "Dataset Discovery" });
  await expect(discovery.getByRole("combobox", { name: "집계 방식" })).toHaveValue("count");
  await discovery.getByRole("combobox", { name: "집계 방식" }).selectOption("null_count");
  await discovery.getByRole("combobox", { name: "분석할 컬럼" }).selectOption("label");
  await discovery.getByRole("combobox", { name: "집계 그룹" }).selectOption("group");
  await discovery.getByRole("button", { name: "샘플 집계" }).click();
  await expect(discovery.getByRole("table").getByText("1", { exact: true })).toBeVisible();
});


test("accepted Job is not reported as failed when Jobs refresh fails", async ({ page }) => {
  let failRefresh = false;
  await page.route("**/api/jobs**", async route => {
    if (route.request().method() === "GET" && failRefresh) {
      await route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"일시적인 목록 오류"}' });
      return;
    }
    if (route.request().method() === "POST") {
      const response = await route.fetch();
      expect(response.status()).toBe(202);
      failRefresh = true;
      await route.fulfill({ response });
      return;
    }
    await route.continue();
  });
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "job-accepted.csv", mimeType: "text/csv",
    buffer: Buffer.from("signal\n1\n2\n"),
  });
  await expect(page.getByRole("heading", { name: "job-accepted.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "탐색" }).click();
  await page.getByRole("button", { name: /전체 Profile 실행/ }).click();
  await expect(page.getByRole("status").filter({ hasText: "Job은 정상 접수됐습니다." })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("audit navigation does not show unrelated dataset tabs", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "audit-navigation.csv", mimeType: "text/csv",
    buffer: Buffer.from("signal\n1\n2\n"),
  });
  await expect(page.getByRole("heading", { name: "audit-navigation.csv" })).toBeVisible();
  await page.getByRole("button", { name: "보안 감사 보기" }).click();
  await expect(page.getByRole("heading", { name: "Security Audit" })).toBeVisible();
  await expect(page.locator(".dataset-hero")).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "탐색" })).toHaveCount(0);
});


test("sample and full Discovery distinguish NULL groups from whole-dataset aggregation", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "nullable-group.csv", mimeType: "text/csv",
    buffer: Buffer.from("group,value\n,1\na,2\n"),
  });
  await expect(page.getByRole("heading", { name: "nullable-group.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "탐색" }).click();
  const discovery = page.getByRole("region", { name: "Dataset Discovery" });
  await discovery.getByRole("combobox", { name: "집계 그룹" }).selectOption("group");
  await discovery.getByRole("button", { name: "샘플 집계" }).click();
  await expect(discovery.getByRole("table").getByText("(NULL 그룹)")).toBeVisible();
  const jobId = await submittedJob(page, "/api/explorations/jobs", () =>
    discovery.getByRole("button", { name: "전체 집계 Job 실행" }).click());
  const job = jobById(page, jobId);
  await expect(job.locator(".status")).toContainText("Succeeded", { timeout: 30000 });
  await expect(job.getByRole("region", { name: "전체 집계 결과" }).getByText("(NULL 그룹)")).toBeVisible();
});


test("late initial Dataset list cannot overwrite successful upload selection", async ({ page }) => {
  let resolveInitial: () => void = () => {};
  let initialFetched: () => void = () => {};
  const gotInitial = new Promise<void>(resolve => { initialFetched = resolve; });
  const delivery = new Promise<void>(resolve => { resolveInitial = resolve; });
  let isFirst = true;
  await page.route("**/api/datasets?*", async route => {
    if (isFirst && route.request().method() === "GET") {
      isFirst = false;
      const response = await route.fetch(); // 업로드 이전의 오래된 스냅샷
      initialFetched();
      await delivery;
      await route.fulfill({ response });
      return;
    }
    await route.continue();
  });
  await page.goto("/");
  await gotInitial;
  await page.locator('input[type="file"]').setInputFiles({
    name: "stale-list.csv", mimeType: "text/csv",
    buffer: Buffer.from("value\n1\n2\n"),
  });
  await expect(page.getByRole("heading", { name: "stale-list.csv" })).toBeVisible();
  resolveInitial();
  await expect(page.getByRole("heading", { name: "stale-list.csv" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "탐색" })).toBeVisible();
});


test("lost HTTP response reuses the original Job submission key", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "replay.csv", mimeType: "text/csv",
    buffer: Buffer.from("value\n1\n2\n"),
  });
  await expect(page.getByRole("heading", { name: "replay.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "탐색" }).click();
  const submitted: Array<{ key: string | undefined; id: string }> = [];
  await page.route("**/api/jobs", async route => {
    if (route.request().method() !== "POST") return route.continue();
    const response = await route.fetch();
    const job = (await response.json()) as { id: string };
    submitted.push({ key: route.request().headers()["idempotency-key"], id: job.id });
    if (submitted.length === 1) await route.abort("failed");
    else await route.fulfill({ response });
  });
  const create = page.getByRole("button", { name: /전체 Profile 실행/ });
  await create.click();
  await expect(page.getByRole("alert")).toBeVisible();
  const secondResponse = page.waitForResponse(res =>
    res.url().endsWith("/api/jobs") && res.request().method() === "POST"
      && res.status() === 202);
  await create.click();
  await secondResponse;
  expect(submitted).toHaveLength(2);
  expect(submitted[0].key).toMatch(/^[0-9a-f-]{36}$/i);
  expect(submitted[1]).toEqual(submitted[0]);
});


test("late Dataset list response does not exit explicitly selected example gallery", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "original-selection.csv", mimeType: "text/csv",
    buffer: Buffer.from("value\n1\n2\n"),
  });
  await expect(page.getByRole("heading", { name: "original-selection.csv" })).toBeVisible();
  let unblock: () => void = () => {};
  let fetched: () => void = () => {};
  const started = new Promise<void>(resolve => { fetched = resolve; });
  const release = new Promise<void>(resolve => { unblock = resolve; });
  let held = false;
  await page.route("**/api/datasets?*", async route => {
    if (route.request().method() === "GET" && !held) {
      held = true;
      const response = await route.fetch();
      fetched();
      await release;
      await route.fulfill({ response });
    } else await route.continue();
  });
  await page.reload();
  await started;
  await page.getByRole("button", { name: "예제 데이터 살펴보기" }).click();
  await expect(page.getByText("예제 데이터로 먼저 살펴보기")).toBeVisible();
  unblock();
  await expect(page.getByText("예제 데이터로 먼저 살펴보기")).toBeVisible();
  await expect(page.locator(".dataset-hero")).toHaveCount(0);
});

test("Job reuses submission key when server accepted it before a 503 response", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "transient-response.csv", mimeType: "text/csv",
    buffer: Buffer.from("value\n1\n2\n"),
  });
  await expect(page.getByRole("heading", { name: "transient-response.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "탐색" }).click();
  const sent: Array<{ key: string | undefined; id: string }> = [];
  await page.route("**/api/jobs", async route => {
    if (route.request().method() !== "POST") return route.continue();
    const response = await route.fetch();
    const job = await response.json() as { id: string };
    sent.push({ key: route.request().headers()["idempotency-key"], id: job.id });
    if (sent.length === 1) {
      await route.fulfill({ status: 503, contentType: "application/json",
        body: JSON.stringify({ detail: "Response unavailable" }) });
    } else await route.fulfill({ response });
  });
  const create = page.getByRole("button", { name: /전체 Profile 실행/ });
  await create.click();
  await expect(page.getByRole("alert")).toContainText("Response unavailable");
  await create.click();
  await expect(page.getByText("작업이 등록되었습니다.")).toBeVisible();
  expect(sent).toHaveLength(2);
  expect(sent[0].key).toBeTruthy();
  expect(sent[1]).toEqual(sent[0]);
});


test("Iceberg browse input never changes the append target or restores stale snapshots", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "append-target.csv", mimeType: "text/csv",
    buffer: Buffer.from("value\n1\n2\n"),
  });
  await expect(page.getByRole("heading", { name: "append-target.csv" })).toBeVisible();
  const appendTarget = page.getByRole("textbox", { name: "Iceberg 테이블명", exact: true });
  const browseTable = page.getByRole("textbox", { name: "Iceberg 테이블 이름" });
  await appendTarget.fill("keep_append_target");
  await browseTable.fill("slow_browse");
  expect(await appendTarget.inputValue()).toBe("keep_append_target");
  let unblock: () => void = () => {};
  let markStarted: () => void = () => {};
  const started = new Promise<void>(resolve => { markStarted = resolve; });
  const release = new Promise<void>(resolve => { unblock = resolve; });
  await page.route("**/api/iceberg/slow_browse/snapshots", async route => {
    markStarted();
    await release;
    await route.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify([{ snapshot_id: "12345", timestamp_ms: 1000 }]) });
  });
  await page.getByRole("button", { name: "Snapshot 조회" }).click();
  await started;
  await browseTable.fill("other_browse");
  const delivered = page.waitForResponse(res => res.url().includes("slow_browse/snapshots"));
  unblock();
  await delivered;
  await expect(page.getByRole("combobox", { name: "Iceberg Snapshot 선택" })).toHaveCount(0);
  await expect(appendTarget).toHaveValue("keep_append_target");
  await expect(browseTable).toHaveValue("other_browse");
});


test("general Dataset query projects, filters and sorts columns", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "generic-query.csv", mimeType: "text/csv",
    buffer: Buffer.from("group,value,private\na,10,hidden\nb,2,secret\na,5,internal\n"),
  });
  await expect(page.getByRole("heading", { name: "generic-query.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "데이터 조회" }).click();
  await page.getByText("컬럼·필터·정렬 선택").click();
  await page.getByLabel("표시할 컬럼 선택").getByLabel("value", { exact: true }).check();
  await page.getByLabel("필터 컬럼").selectOption("group");
  await page.getByLabel("비교 값").fill("a");
  await page.getByLabel("정렬 컬럼").selectOption("value");
  await page.getByLabel("내림차순").check();
  await page.getByRole("button", { name: "조회 적용" }).click();
  await expect(page.locator("#sos-panel-preview thead th")).toHaveCount(1);
  await expect(page.locator("#sos-panel-preview tbody tr")).toHaveCount(2);
  await expect(page.locator("#sos-panel-preview tbody tr").first()).toContainText("10");
  await expect(page.locator("#sos-panel-preview tbody tr").last()).toContainText("5");
});

test("Feature Builder can select columns and multiple grouping keys", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "grouped-features.csv", mimeType: "text/csv",
    buffer: Buffer.from("site,machine,time,value,private\na,x,1,10,k\na,x,2,20,z\nb,y,1,30,q\n"),
  });
  await expect(page.getByRole("heading", { name: "grouped-features.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "변환" }).click();
  await page.getByLabel("변환 연산", { exact: true }).selectOption("select");
  await page.getByRole("group", { name: "유지할 컬럼" }).getByLabel("site").check();
  await page.getByRole("group", { name: "유지할 컬럼" }).getByLabel("machine").check();
  await page.getByRole("group", { name: "유지할 컬럼" }).getByLabel("time").check();
  await page.getByRole("group", { name: "유지할 컬럼" }).getByLabel("value").check();
  await page.getByRole("button", { name: "+ 단계 추가" }).click();
  await page.getByLabel("시간·정렬 컬럼").selectOption("time");
  await page.getByLabel("그룹 컬럼").selectOption(["site", "machine"]);
  await page.getByRole("button", { name: "샘플 변환 확인" }).click();
  await expect(page.getByText("변환 미리보기 · 전체 입력 기준 계산")).toBeVisible();
  await expect(page.locator("#sos-panel-features .result th")).toHaveCount(4);
});


test("loading older Jobs survives a concurrent background refresh", async ({ page }) => {
  let requestedOlder = 0;
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/jobs?limit=20*", async route => {
    const url = new URL(route.request().url());
    if (url.searchParams.has("cursor")) {
      requestedOlder++;
      await held;
      return route.fulfill({ status: 200, json: {
        items: [{
          id: "older-page-job", dataset_id: "old-dataset", kind: "profile",
          status: "Succeeded", created_at: "2026-01-01T00:00:00Z",
          attempt_count: 0, spec: {}, result: null,
        }],
        next_cursor: null,
      } });
    }
    const response = await route.fetch();
    const current = await response.json() as { items: unknown[]; next_cursor: string | null };
    return route.fulfill({ response, json: { ...current, next_cursor: "older-page-token" } });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "전체 Jobs 보기" }).click();
  try {
    await page.getByRole("button", { name: "이전 작업 더 보기 →" }).click();
    await expect.poll(() => requestedOlder).toBeGreaterThan(0);
    await page.getByRole("button", { name: "새로고침" }).click();
  } finally {
    release();
  }
  await expect(page.getByText("older-page-job")).toBeVisible();
});


test("an older Jobs GET cannot overwrite a newer manual refresh", async ({ page }) => {
  let seen = 0;
  let delivered = false;
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/jobs?limit=20", async route => {
    seen++;
    if (seen !== 1) return route.continue();
    const response = await route.fetch();
    const current = await response.json() as { items: unknown[]; next_cursor: string | null };
    await held;
    await route.fulfill({ response, json: {
      ...current,
      items: [{
        id: "stale-polling-result",
        dataset_id: "stale-dataset",
        kind: "profile",
        status: "Running",
        created_at: "2026-01-01T00:00:00Z",
        attempt_count: 0,
        spec: {},
      }],
    } });
    delivered = true;
  });
  await page.goto("/");
  await expect.poll(() => seen).toBeGreaterThanOrEqual(1);
  const beforeRefresh = seen;
  await page.getByRole("button", { name: "전체 Jobs 보기" }).click();
  await page.getByRole("button", { name: "새로고침" }).click();
  await expect.poll(() => seen).toBeGreaterThan(beforeRefresh);
  release();
  await expect.poll(() => delivered).toBe(true);
  await expect(page.getByText("stale-polling-result")).toHaveCount(0);
});

test("late feature preview never reappears after Dataset reload", async ({ page }) => {
  await page.goto("/");
  const input = page.locator('input[type="file"]');
  await input.setInputFiles({
    name: "feature-preview-race.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("group,value\na,1\nb,2\n"),
  });
  await expect(page.getByRole("heading", { name: "feature-preview-race.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "변환" }).click();
  await page.getByLabel("컬럼", { exact: true }).selectOption("value");
  await page.getByRole("button", { name: "+ 단계 추가" }).click();
  let release: (() => void) | undefined;
  let intercepted = 0;
  await page.route("**/api/features/preview", async route => {
    intercepted++;
    await new Promise<void>(resolve => { release = resolve; });
    await route.continue();
  });
  await page.getByRole("button", { name: "샘플 변환 확인" }).click();
  await expect.poll(() => intercepted).toBe(1);
  await page.getByRole("button", { name: "데이터 다시 불러오기" }).click();
  release?.();
  await expect(page.getByRole("button", { name: "샘플 변환 확인" })).toBeEnabled();
  await expect(page.getByText("변환 미리보기 · 전체 입력 기준 계산")).toHaveCount(0);
});

test("editing Dataset Query while its response is pending never renders stale rows", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "query-race.csv", mimeType: "text/csv",
    buffer: Buffer.from("group,value\na,10\nb,20\na,30\n"),
  });
  await expect(page.getByRole("heading", { name: "query-race.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "데이터 조회" }).click();
  await page.getByText("컬럼·필터·정렬 선택").click();
  await page.getByLabel("필터 컬럼").selectOption("group");
  await page.getByLabel("비교 값").fill("a");
  let release: () => void = () => {};
  let received: () => void = () => {};
  const started = new Promise<void>(resolve => { received = resolve; });
  const held = new Promise<void>(resolve => { release = resolve; });
  let intercepted = false;
  await page.route("**/api/datasets/*/query", async route => {
    if (route.request().method() !== "POST" || intercepted) return route.continue();
    intercepted = true;
    const response = await route.fetch();
    received();
    await held;
    await route.fulfill({ response });
  });
  await page.getByRole("button", { name: "조회 적용" }).click();
  await started;
  await page.getByLabel("비교 값").fill("b");
  release();
  await expect(page.getByRole("button", { name: "조회 적용" })).toBeEnabled();
  await expect(page.locator("#sos-panel-preview tbody tr")).toHaveCount(3);
  await page.getByRole("button", { name: "조회 적용" }).click();
  await expect(page.locator("#sos-panel-preview tbody tr")).toHaveCount(1);
  await expect(page.locator("#sos-panel-preview tbody tr")).toContainText("20");
});

test("unknown CSV opens Understanding and transfers a candidate to editable Exploration", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "unknown.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("department,score\nalpha,3\nbeta,5\nalpha,7\nbeta,9\n"),
  });
  await expect(page.getByRole("heading", { name: "unknown.csv" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "개요" })).toHaveAttribute("aria-selected", "true");
  const panel = page.getByRole("tabpanel", { name: "개요" });
  await expect(panel.getByText("컬럼 특성")).toBeVisible({ timeout: 30000 });
  await expect(panel.getByText("그룹별 수치 비교")).toBeVisible();
  await panel.getByRole("searchbox", { name: "프로파일 컬럼 검색" }).fill("score");
  await expect(panel.getByRole("cell", { name: "score" })).toBeVisible();
  await expect(panel.getByRole("cell", { name: "department" })).toHaveCount(0);
  await panel.getByRole("searchbox", { name: "프로파일 컬럼 검색" }).fill("");
  await expect(panel.getByRole("heading", { name: "탐색 후보" })).toBeVisible();
  let explorationRequests = 0;
  page.on("request", req => {
    if (/\/api\/datasets\/[^/]+\/explorations$/.test(req.url()) &&
      req.method() === "POST") explorationRequests += 1;
  });
  await panel.getByRole("button", { name: "탐색 조건 보기" }).first().click();
  await expect(page.getByRole("tab", { name: "탐색" })).toHaveAttribute("aria-selected", "true");
  const exploration = page.getByRole("tabpanel", { name: "탐색" });
  await expect(exploration.getByText(/탐색 후보에서 가져온 조건/)).toBeVisible();
  await expect(exploration.getByLabel("집계 방식")).toHaveValue("mean");
  await expect(exploration.getByLabel("분석할 컬럼")).toHaveValue("score");
  await expect(exploration.getByLabel("집계 그룹")).toHaveValue("department");
  expect(explorationRequests).toBe(0);
  await exploration.getByLabel("집계 방식").selectOption("max");
  await page.getByRole("tab", { name: "개요" }).click();
  await page.getByRole("tab", { name: "탐색" }).click();
  await expect(exploration.getByLabel("집계 방식")).toHaveValue("max");
  expect(explorationRequests).toBe(0);
  await exploration.getByRole("button", { name: "샘플 집계" }).click();
  await expect.poll(() => explorationRequests).toBe(1);
  await expect(exploration.getByText(/Dataset 판본/)).toBeVisible();
  await page.reload();
  await expect(page.getByRole("tab", { name: "개요" })).toBeVisible();
  await expect(page.getByText("컬럼 특성")).toBeVisible({ timeout: 30000 });
});


test("Data Explorer shell, instance name and tab-scoped loading", async ({ page }) => {
  const previewRequests: string[] = [];
  const profileRequests: string[] = [];
  const overviewRequests: string[] = [];
  page.on("request", request => {
    const url = request.url();
    if (/\/api\/datasets\/[^/]+\/preview\?/.test(url)) previewRequests.push(url);
    if (/\/api\/datasets\/[^/]+\/profile$/.test(url)) profileRequests.push(url);
    if (/\/api\/datasets\/[^/]+\/overview$/.test(url)) overviewRequests.push(url);
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "데이터 탐색" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "전역 메뉴" })).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles({
    name: "ui-explorer.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("group,value\na,1\nb,2\n"),
  });
  await expect(page.getByRole("heading", { name: "ui-explorer.csv" })).toBeVisible();
  await expect.poll(() => overviewRequests.filter(url => url.includes("/overview")).length).toBeGreaterThan(0);
  const overviewCount = overviewRequests.length;
  await expect(page.getByRole("tab", { name: "개요" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "데이터 프로파일" })).toBeVisible();
  expect(previewRequests.length).toBe(0);
  expect(profileRequests.length).toBe(0);
  await page.getByRole("tab", { name: "데이터 조회" }).click();
  await expect(page.getByText("2 rows shown")).toBeVisible();
  expect(previewRequests.length).toBe(1);
  await page.getByRole("tab", { name: "탐색" }).click();
  await expect(page.getByRole("heading", { name: "탐색 조건" })).toBeVisible();
  expect(overviewRequests.length).toBe(overviewCount);
  await page.getByRole("tab", { name: "개요" }).click();
  await page.getByRole("tab", { name: "데이터 조회" }).click();
  await expect(page.getByText("2 rows shown")).toBeVisible();
  expect(previewRequests.length).toBe(1);
});

test("administrator changes instance name and it survives browser reload", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "표시명 설정" }).click();
  await page.getByLabel("인스턴스 표시명").fill("UX Research Workspace");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("UX Research Workspace")).toBeVisible();
  await page.reload();
  await expect(page.getByText("UX Research Workspace")).toBeVisible();
});

test("Data Explorer navigation stays usable at mobile width", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "데이터 탐색" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "전역 메뉴" })).toBeVisible();
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(390);
});


test("Dataset refresh re-fetches the current Understanding profile", async ({ page }) => {
  let requests = 0;
  page.on("request", request => {
    if (/\/api\/datasets\/[^/]+\/understanding$/.test(request.url())
      && request.method() === "GET") requests += 1;
  });
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "refresh-understanding.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("label,metric\na,1\nb,3\n"),
  });
  await expect(page.getByRole("tab", { name: "개요" })).toHaveAttribute("aria-selected", "true");
  await expect.poll(() => requests).toBeGreaterThan(0);
  const firstCount = requests;
  await page.getByRole("button", { name: "데이터 다시 불러오기" }).click();
  await expect.poll(() => requests).toBeGreaterThan(firstCount);
});


test("operator navigation marks the active operational section", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "전체 Jobs 보기" }).click();
  await expect(page.getByRole("navigation", { name: "전역 메뉴" })
    .getByRole("button", { name: "전체 Jobs 보기" })).toHaveAttribute("aria-current", "page");
  await page.getByRole("button", { name: "저장소 상태 보기" }).click();
  await expect(page.getByRole("navigation", { name: "전역 메뉴" })
    .getByRole("button", { name: "저장소 상태 보기" })).toHaveAttribute("aria-current", "page");
});


test("switching Datasets never displays another Dataset's cached Preview", async ({ page }) => {
  await page.goto("/");
  const input = page.locator('input[type="file"]');
  await input.setInputFiles({
    name: "source-a.csv", mimeType: "text/csv",
    buffer: Buffer.from("key,value\nFIRST_ONLY,11\n"),
  });
  await expect(page.getByRole("heading", { name: "source-a.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "데이터 조회" }).click();
  await expect(page.getByRole("cell", { name: "FIRST_ONLY" })).toBeVisible();

  await input.setInputFiles({
    name: "source-b.csv", mimeType: "text/csv",
    buffer: Buffer.from("key,value\nSECOND_ONLY,22\n"),
  });
  await expect(page.getByRole("heading", { name: "source-b.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "데이터 조회" }).click();
  await expect(page.getByRole("cell", { name: "FIRST_ONLY" })).toHaveCount(0);
  await expect(page.getByRole("cell", { name: "SECOND_ONLY" })).toBeVisible();
});


test("Data Explorer supports WCAG 320px reflow without page-wide overflow", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 });
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "전역 메뉴" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "데이터 탐색" })).toBeVisible();
  const pageWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(pageWidth).toBeLessThanOrEqual(320);
});


test("Dataset switch clears a previously chosen exploration candidate", async ({ page }) => {
  await page.goto("/");
  const input = page.locator('input[type="file"]');
  await input.setInputFiles({
    name: "candidate-a.csv", mimeType: "text/csv",
    buffer: Buffer.from("department,score\nalpha,3\nbeta,5\nalpha,7\n"),
  });
  await expect(page.getByRole("heading", { name: "candidate-a.csv" })).toBeVisible();
  const overview = page.getByRole("tabpanel", { name: "개요" });
  await expect(overview.getByRole("heading", { name: "탐색 후보" })).toBeVisible({ timeout: 30000 });
  await overview.getByRole("button", { name: "탐색 조건 보기" }).first().click();
  await expect(page.getByRole("tabpanel", { name: "탐색" })
    .getByText(/탐색 후보에서 가져온 조건/)).toBeVisible();
  await input.setInputFiles({
    name: "candidate-b.csv", mimeType: "text/csv",
    buffer: Buffer.from("region,amount\nseoul,1\nbusan,2\n"),
  });
  await expect(page.getByRole("heading", { name: "candidate-b.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "탐색" }).click();
  const exploration = page.getByRole("tabpanel", { name: "탐색" });
  await expect(exploration.getByText(/탐색 후보에서 가져온 조건/)).toHaveCount(0);
  await expect(exploration.getByLabel("집계 그룹")).toHaveValue("");
  await expect(exploration.getByLabel("분석할 컬럼")).not.toHaveValue("score");
});
