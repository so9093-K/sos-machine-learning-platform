import { expect, test, type Page } from "@playwright/test";

// 제품 인증을 우회하지 않고 브라우저 Context에 임시 테스트 Token을 전달한다.
test.beforeEach(async ({ context }) => {
  const token = process.env.SOS_E2E_ADMIN_TOKEN;
  if (!token) throw new Error("격리 Workspace E2E 관리자 Token이 필요합니다.");
  await context.setExtraHTTPHeaders({ Authorization: `Bearer ${token}` });
});

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


test("헤더 없는 한국어 TXT는 등록 전 확인하고 첫 행을 보존한다", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "reviews.txt",
    mimeType: "text/plain",
    buffer: Buffer.from('5\t배공빠르고 굿\n2\t표현에 "따옴표"가 있어요\n'),
  });
  await expect(page.getByRole("heading", { name: "파일 구조 확인 · 등록 전" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "unnamed_1" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "unnamed_2" })).toBeVisible();
  await page.getByRole("button", { name: "이 설정으로 Dataset 등록" }).click();
  await expect(page.getByRole("heading", { name: "reviews.txt" })).toBeVisible();
  await page.getByRole("tab", { name: "데이터 조회" }).click();
  const table = page.locator("#sos-panel-preview table").first();
  await expect(table.getByRole("columnheader", { name: "unnamed_1" })).toBeVisible();
  await expect(table.getByText("배공빠르고 굿")).toBeVisible();
});

test("TSV 업로드 실패 후 파일을 재선택하지 않고 설정을 수정해 재시도한다", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "retry-import.tsv",
    mimeType: "text/tab-separated-values",
    buffer: Buffer.from("score\treview\n5\tgood\n2\tbad\n"),
  });
  const heading = page.getByRole("heading", { name: "파일 구조 확인 · 등록 전" });
  await expect(heading).toBeVisible();
  const header = page.getByRole("combobox", { name: "첫 행의 의미" });
  await header.selectOption("header");
  let rejected = false;
  await page.route("**/api/datasets/files", async route => {
    if (rejected) return route.continue();
    rejected = true;
    await route.fulfill({
      status: 422, contentType: "application/json",
      body: JSON.stringify({ code: "SOS-UPLOAD-INVALID", message: "invalid input" }),
    });
  });
  await page.getByRole("button", { name: "이 설정으로 Dataset 등록" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(heading).toBeVisible();
  await expect(header).toHaveValue("header");
  await page.unroute("**/api/datasets/files");
  await page.getByRole("button", { name: "이 설정으로 Dataset 등록" }).click();
  await expect(page.getByRole("heading", { name: "retry-import.tsv" })).toBeVisible();
  await expect(heading).toHaveCount(0);
});

test("example onboarding opens the selected Dataset without automatic processing", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "예제 데이터 살펴보기" }).click();
  await expect(page.getByText("예제 데이터로 먼저 살펴보기")).toBeVisible();
  await expect(page.getByText("합성 데이터입니다. 선택할 때만 일반 데이터셋으로 등록됩니다.")).toBeVisible();
  await page.getByRole("button", { name: /이 예제로 탐색 시작/ }).first().click();
  await expect(page.getByRole("heading", { name: "example-machine-sensors.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "탐색" }).click();
  await expect(page.getByText("분석한 행", { exact: true })).toBeVisible();
  // 변환/Job 실행의 정확성은 별도 실제 브라우저 흐름과 DB 통합 테스트가 담당한다.
  await expect(page.getByRole("button", { name: "데이터 준비", exact: true })).toBeVisible();
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
  await page.getByRole("button", { name: "데이터 준비", exact: true }).click();
  await page.getByLabel("변환 연산", { exact: true }).selectOption("lag");
  await page.getByLabel("컬럼", { exact: true }).selectOption("temperature");
  await page.getByLabel("시간·정렬 컬럼").selectOption("time");
  await page.getByLabel("그룹 컬럼").selectOption("machine_id");
  await page.getByRole("button", { name: "+ 단계 추가" }).click();
  await page.getByRole("button", { name: "변환 결과 미리보기" }).click();
  await expect(page.getByText("변환 미리보기 · 전체 입력 기준 연산")).toBeVisible();
  await page.getByLabel("그룹 컬럼").selectOption("");
  await expect(page.getByText("변환 미리보기 · 전체 입력 기준 연산")).toHaveCount(0);
  await page.getByRole("button", { name: "변환 결과 미리보기" }).click();
  await expect(page.getByRole("alert")).toContainText("unique within each group");
  await page.getByLabel("그룹 컬럼").selectOption("machine_id");
  await page.getByRole("button", { name: "변환 결과 미리보기" }).click();
  await expect(page.getByText("변환 미리보기 · 전체 입력 기준 연산")).toBeVisible();
});


test("file upload is operable through keyboard", async ({ page }) => {
  await page.goto("/");
  const upload = page.getByRole("button", { name: "CSV·Parquet·TSV·TXT 파일 업로드" });
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
  await page.getByRole("button", { name: "데이터 준비", exact: true }).click();
  await page.getByLabel("변환 연산", { exact: true }).selectOption("lag");
  await page.getByLabel("컬럼", { exact: true }).selectOption("temperature");
  await page.getByLabel("시간·정렬 컬럼").selectOption("time");
  await page.getByLabel("그룹 컬럼").selectOption("machine_id");
  await page.getByRole("button", { name: "+ 단계 추가" }).click();
  await page.getByLabel("컬럼", { exact: true }).selectOption("vibration");
  await page.getByRole("button", { name: "데이터 탐색", exact: true }).click();
  await page.getByRole("button", { name: "데이터 준비", exact: true }).click();
  await expect(page.getByLabel("컬럼", { exact: true })).toHaveValue("vibration");
  await expect(page.locator(".step")).toHaveCount(1);
  await page.getByRole("button", { name: "데이터 다시 불러오기" }).click();
  await expect(page.getByLabel("컬럼", { exact: true })).toHaveValue("vibration");
  await expect(page.getByLabel("시간·정렬 컬럼")).toHaveValue("time");
  await expect(page.getByLabel("그룹 컬럼")).toHaveValue("machine_id");
  await expect(page.locator(".step")).toHaveCount(1);
  await page.getByRole("button", { name: "변환 결과 미리보기" }).click();
  await expect(page.getByText("변환 미리보기 · 전체 입력 기준 연산")).toBeVisible();
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
  let failedRefreshes = 0;
  await page.route("**/api/datasets?*", async route => {
    if (route.request().method() === "GET" && failRefresh) {
      await route.fulfill({ status: 503, contentType: "application/json",
        body: JSON.stringify({ detail: "List temporarily unavailable" }) });
      failedRefreshes++;
      return;
    }
    await route.continue();
  });
  await page.route("**/api/datasets/files", async route => {
    const response = await route.fetch();
    expect(response.status()).toBe(201);
    failRefresh = true;
    await route.fulfill({ response });
  });
  try {
    await page.goto("/");
    await page.locator('input[type="file"]').setInputFiles({
      name: "published.csv", mimeType: "text/csv",
      buffer: Buffer.from("signal\n1\n2\n"),
    });
    await expect(page.getByRole("heading", { name: "published.csv" })).toBeVisible();
    // 503이 실제로 전달됐는지 검증한다. 백그라운드 GET 경합으로 이전
    // 목록 요청이 대체됐다면 등록 성공 안내를 유지하는 것도 올바른 동작이다.
    await expect.poll(() => failedRefreshes).toBeGreaterThan(0);
    // 등록 후 후속 목록 요청까지 처리된 시점의 최종 UI를 검사한다.
    await expect(page.locator('input[type="file"]')).toBeEnabled();
    await expect(page.getByRole("status").filter({
      hasText: /파일 등록이 완료됐습니다\.|파일은 정상 등록됐습니다\./,
    })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
  } finally {
    await page.unrouteAll({ behavior: "wait" });
  }
});


test("upload indicates pending transfer and completes after server response", async ({ page }) => {
  let releaseResponse: () => void = () => {};
  let releaseRefresh: () => void = () => {};
  let registered = false;
  let heldRefreshCount = 0;
  const heldResponse = new Promise<void>(resolve => { releaseResponse = resolve; });
  const heldRefresh = new Promise<void>(resolve => { releaseRefresh = resolve; });
  await page.route("**/api/datasets?*", async route => {
    if (!registered) return route.continue();
    heldRefreshCount++;
    await heldRefresh;
    return route.continue();
  });
  await page.route("**/api/datasets/files", async route => {
    const response = await route.fetch();
    await heldResponse;
    registered = true;
    await route.fulfill({ response });
  });
  try {
    await page.goto("/");
    await page.locator('input[type="file"]').setInputFiles({
      name: "progress.csv", mimeType: "text/csv",
      buffer: Buffer.from("time,value\n1,2\n2,3\n"),
    });
    // 서버의 201 전에 '전송 중', 201 뒤에는 목록 GET 완료 전에 등록 성공 안내가 보여야 한다.
    await expect(page.getByText(/전송 중 ·/)).toBeVisible();
    releaseResponse();
    await expect(page.getByRole("heading", { name: "progress.csv" })).toBeVisible();
    await expect.poll(() => heldRefreshCount).toBeGreaterThan(0);
    await expect(page.getByText("파일 등록이 완료됐습니다.", { exact: false })).toBeVisible();
  } finally {
    releaseResponse();
    releaseRefresh();
    // 지연 요청과 Route 콜백을 테스트 종료 전에 정리하되 오류를 무시하지 않는다.
    await page.unrouteAll({ behavior: "wait" });
  }
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
  await expect(discovery.getByRole("button", { name: "부분 데이터 집계" })).toBeEnabled();
  await discovery.getByRole("button", { name: "부분 데이터 집계" }).click();
  await expect(discovery.getByText(/부분 데이터 분석 · 최대/)).toBeVisible();
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
  await page.getByRole("button", { name: /전체 데이터 프로파일 실행/ }).click();
  await expect(page.getByRole("status").filter({ hasText: "Job은 정상 접수됐습니다." })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("Discovery sample renders a NULL group distinctly", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "nullable-group.csv", mimeType: "text/csv",
    buffer: Buffer.from("group,value\n,1\na,2\n"),
  });
  await expect(page.getByRole("heading", { name: "nullable-group.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "탐색" }).click();
  const discovery = page.getByRole("region", { name: "Dataset Discovery" });
  await discovery.getByRole("combobox", { name: "집계 그룹" }).selectOption("group");
  await discovery.getByRole("button", { name: "부분 데이터 집계" }).click();
  await expect(discovery.getByRole("table").getByText("(NULL 그룹)")).toBeVisible();
  // 전체 집계의 NULL 의미는 test_discovery.py의 실제 Worker/DB 검증이 담당한다.
  await expect(discovery.getByRole("button", { name: "전체 집계 Job 실행" })).toBeEnabled();
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
  const create = page.getByRole("button", { name: /전체 데이터 프로파일 실행/ });
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


test("Iceberg browse input never changes the append target or restores stale snapshots", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "append-target.csv", mimeType: "text/csv",
    buffer: Buffer.from("value\n1\n2\n"),
  });
  await expect(page.getByRole("heading", { name: "append-target.csv" })).toBeVisible();
  await page.getByRole("button", { name: "데이터 준비", exact: true }).click();
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
  await page.getByLabel("비교 값").fill("b");
  await expect(page.getByRole("status").getByText(/변경 사항 미적용/)).toBeVisible();
  // 마지막 적용 결과는 남아 있고, 편집 중인 조건만 아직 반영되지 않는다.
  await expect(page.locator("#sos-panel-preview tbody tr")).toHaveCount(2);
  await page.getByRole("tab", { name: "데이터 이해" }).click();
  await page.getByRole("tab", { name: "데이터 조회" }).click();
  await page.getByText("컬럼·필터·정렬 선택").click();
  await expect(page.getByLabel("비교 값")).toHaveValue("b");
  await expect(page.getByRole("status").getByText(/변경 사항 미적용/)).toBeVisible();
  await page.getByRole("button", { name: "조회 적용" }).click();
  await expect(page.locator("#sos-panel-preview tbody tr")).toHaveCount(1);
  await expect(page.locator("#sos-panel-preview tbody tr")).toContainText("2");
  await page.getByRole("button", { name: "데이터 다시 불러오기" }).click();
  await expect(page.getByText(/변경 사항 미적용.*기본 미리보기/)).toBeVisible();
  await expect(page.getByLabel("비교 값")).toHaveValue("b");
});

test("Feature Builder can select columns and multiple grouping keys", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "grouped-features.csv", mimeType: "text/csv",
    buffer: Buffer.from("site,machine,time,value,private\na,x,1,10,k\na,x,2,20,z\nb,y,1,30,q\n"),
  });
  await expect(page.getByRole("heading", { name: "grouped-features.csv" })).toBeVisible();
  await page.getByRole("button", { name: "데이터 준비", exact: true }).click();
  await page.getByLabel("변환 연산", { exact: true }).selectOption("select");
  await page.getByRole("group", { name: "유지할 컬럼" }).getByLabel("site").check();
  await page.getByRole("group", { name: "유지할 컬럼" }).getByLabel("machine").check();
  await page.getByRole("group", { name: "유지할 컬럼" }).getByLabel("time").check();
  await page.getByRole("group", { name: "유지할 컬럼" }).getByLabel("value").check();
  await page.getByRole("button", { name: "+ 단계 추가" }).click();
  await page.getByLabel("시간·정렬 컬럼").selectOption("time");
  await page.getByLabel("그룹 컬럼").selectOption(["site", "machine"]);
  await page.getByRole("button", { name: "변환 결과 미리보기" }).click();
  await expect(page.getByText("변환 미리보기 · 전체 입력 기준 연산")).toBeVisible();
  await expect(page.locator('[aria-label="데이터 준비"] .result th')).toHaveCount(4);
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
  try {
    await page.goto("/");
    await page.getByRole("button", { name: "전체 Jobs 보기" }).click();
    await page.getByRole("button", { name: "이전 작업 더 보기 →" }).click();
    await expect.poll(() => requestedOlder).toBeGreaterThan(0);
    await page.getByRole("button", { name: "새로고침" }).click();
    release();
    await expect(page.getByText("older-page-job")).toBeVisible();
  } finally {
    release();
    // 진행 중인 route.fetch 콜백까지 완료시켜 다음 테스트의 종료와 경합하지 않는다.
    await page.unrouteAll({ behavior: "wait" });
  }
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
  try {
    await page.goto("/");
    await expect.poll(() => seen).toBeGreaterThanOrEqual(1);
    const beforeRefresh = seen;
    await page.getByRole("button", { name: "전체 Jobs 보기" }).click();
    await page.getByRole("button", { name: "새로고침" }).click();
    await expect.poll(() => seen).toBeGreaterThan(beforeRefresh);
    release();
    await expect.poll(() => delivered).toBe(true);
    await expect(page.getByText("stale-polling-result")).toHaveCount(0);
  } finally {
    release();
    // 늦게 도착한 요청이 UI 결과를 덮어쓰지 않는 계약은 유지하면서 콜백을 모두 회수한다.
    await page.unrouteAll({ behavior: "wait" });
  }
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
  await page.getByRole("button", { name: "데이터 준비", exact: true }).click();
  await page.getByLabel("컬럼", { exact: true }).selectOption("value");
  await page.getByRole("button", { name: "+ 단계 추가" }).click();
  let release: (() => void) | undefined;
  let intercepted = 0;
  await page.route("**/api/features/preview", async route => {
    intercepted++;
    await new Promise<void>(resolve => { release = resolve; });
    await route.continue();
  });
  await page.getByRole("button", { name: "변환 결과 미리보기" }).click();
  await expect.poll(() => intercepted).toBe(1);
  await page.getByRole("button", { name: "데이터 다시 불러오기" }).click();
  release?.();
  await expect(page.getByRole("button", { name: "변환 결과 미리보기" })).toBeEnabled();
  await expect(page.getByText("변환 미리보기 · 전체 입력 기준 연산")).toHaveCount(0);
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

test("administrator changes instance name and it survives browser reload", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "표시명 설정" }).click();
  await page.getByLabel("인스턴스 표시명").fill("UX Research Workspace");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("UX Research Workspace")).toBeVisible();
  await page.reload();
  await expect(page.getByText("UX Research Workspace")).toBeVisible();
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


test("Data Explorer and Data Preparation remain independent workspaces", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "boundary.csv", mimeType: "text/csv",
    buffer: Buffer.from("category,value\na,1\nb,2\n"),
  });
  await expect(page.getByRole("tab", { name: "데이터 이해" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "변환" })).toHaveCount(0);
  await page.getByRole("button", { name: "데이터 준비", exact: true }).click();
  await expect(page.getByRole("heading", { name: "데이터 준비" })).toBeVisible();
  await expect(page.getByRole("tablist", { name: "데이터 작업 영역" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "데이터 준비", exact: true }))
    .toHaveAttribute("aria-current", "page");
  await page.getByLabel("변환 연산", { exact: true }).selectOption("select");
  await page.getByRole("button", { name: "데이터 탐색", exact: true }).click();
  await expect(page.getByRole("tab", { name: "데이터 이해" })).toBeVisible();
  await page.getByRole("button", { name: "데이터 준비", exact: true }).click();
  await expect(page.getByLabel("변환 연산", { exact: true })).toHaveValue("select");
});

test("Wide Dataset preview defaults to a bounded visible column set", async ({ page }) => {
  await page.goto("/");
  const names = Array.from({ length: 15 }, (_, i) => `metric_${i}`);
  await page.locator('input[type="file"]').setInputFiles({
    name: "wide-preview.csv", mimeType: "text/csv",
    buffer: Buffer.from(names.join(",") + "\n" + names.map((_, i) => i + 1).join(",") + "\n"),
  });
  await expect(page.getByRole("heading", { name: "wide-preview.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "데이터 조회" }).click();
  await expect(page.getByRole("columnheader")).toHaveCount(12);
  await expect(page.getByText("전체 15개 컬럼 중 12개 표시", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "전체 컬럼 보기" }).click();
  await expect(page.getByRole("columnheader")).toHaveCount(15);
  await page.getByRole("button", { name: "첫 12개 컬럼만 보기" }).click();
  await expect(page.getByRole("columnheader")).toHaveCount(12);
  // 명시적으로 13개를 요청하면 기본 12개 표시 제한을 적용하지 않는다.
  await page.getByText("컬럼·필터·정렬 선택").click();
  const chosen = page.getByRole("group", { name: "표시할 컬럼 선택" });
  for (const name of names.slice(0, 13)) {
    await chosen.getByRole("checkbox", { name, exact: true }).check();
  }
  await page.getByRole("button", { name: "조회 적용" }).click();
  await expect(page.getByRole("columnheader")).toHaveCount(13);
  await expect(page.getByText("선택한 컬럼 전부 표시", { exact: false })).toBeVisible();
});


test("EDA asks for explicit value and horizontal axis before drawing a trend", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "chart-selection.csv", mimeType: "text/csv",
    buffer: Buffer.from("sequence,measure\n1,10\n2,20\n3,30\n"),
  });
  // 업로드 완료 및 프로파일 응답보다 먼저 과거 Dataset의 탐색 화면을 읽지 않는다.
  await expect(page.getByRole("heading", { name: "chart-selection.csv" })).toBeVisible();
  await page.getByRole("tab", { name: "탐색" }).click();
  await expect(page.getByRole("heading", { name: "컬럼 프로파일" })).toBeVisible();
  await expect(page.getByRole("img", { name: "조회 행 순서의 값 차트" })).toHaveCount(0);
  await page.getByRole("combobox", { name: "값 컬럼" }).selectOption("measure");
  await expect(page.getByRole("img", { name: "조회 행 순서의 값 차트" })).toHaveCount(0);
  await page.getByRole("combobox", { name: "가로축 컬럼" }).selectOption("sequence");
  await expect(page.getByRole("img", { name: "조회 행 순서의 값 차트" })).toBeVisible();
});


test("Data Preparation reports Preview failures and can retry", async ({ page }) => {
  const previewEndpoint = /\/api\/datasets\/[^/]+\/preview\?limit=100/;
  await page.route(previewEndpoint, async route => {
    await route.fulfill({ status: 503, contentType: "application/json",
      body: JSON.stringify({ detail: "preview temporarily unavailable" }) });
  });
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "preparation-retry.csv", mimeType: "text/csv",
    buffer: Buffer.from("metric\n1\n2\n"),
  });
  // 비동기 파일 등록으로 이전 Dataset을 대상으로 실패·재시도를 검증하지 않는다.
  await expect(page.getByRole("heading", { name: "preparation-retry.csv" })).toBeVisible();
  await page.getByRole("button", { name: "데이터 준비", exact: true }).click();
  await expect(page.getByRole("alert").getByText(/preview temporarily unavailable/)).toBeVisible();
  await expect(page.getByLabel("변환 연산", { exact: true })).toHaveCount(0);
  await page.unroute(previewEndpoint);
  await page.getByRole("button", { name: "데이터 준비 미리보기 다시 시도" }).click();
  await expect(page.getByLabel("변환 연산", { exact: true })).toBeVisible();
});

test("Registering another Dataset inside Preparation does not force Explorer navigation", async ({ page }) => {
  await page.goto("/");
  // 다른 테스트가 등록한 Dataset이 초기 선택됐더라도 '미선택' 상태를 명시적으로 검증한다.
  await page.getByRole("button", { name: "예제 데이터 살펴보기" }).click();
  await page.getByRole("button", { name: "데이터 준비", exact: true }).click();
  await expect(page.getByRole("heading", { name: "데이터 준비 시작" })).toBeVisible();
  const input = page.locator('input[type="file"]');
  await input.setInputFiles({
    name: "prep-source-a.csv", mimeType: "text/csv",
    buffer: Buffer.from("value\n1\n2\n"),
  });
  await page.getByRole("button", { name: "데이터 준비", exact: true }).click();
  await expect(page.getByLabel("변환 연산", { exact: true })).toBeVisible();
  await input.setInputFiles({
    name: "prep-source-b.csv", mimeType: "text/csv",
    buffer: Buffer.from("score\n10\n20\n"),
  });
  await expect(page.getByRole("heading", { name: "prep-source-b.csv" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "데이터 준비", exact: true })).toBeVisible();
  await expect(page.getByLabel("변환 연산", { exact: true })).toBeVisible();
  await expect(page.getByRole("tablist", { name: "데이터 작업 영역" })).toHaveCount(0);
});

test("ML Workbench submits a training Job from selected features", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "ml-ui-smoke.csv", mimeType: "text/csv",
    buffer: Buffer.from("temperature,pressure\n1,10\n2,11\n3,12\n4,13\n5,14\n"),
  });
  await expect(page.getByRole("heading", { name: "ml-ui-smoke.csv" })).toBeVisible();
  await page.getByRole("button", { name: "ML Workbench" }).click();
  const submit = page.getByRole("button", { name: "학습 Job 제출" });
  await expect(submit).toBeDisabled();
  await page.getByRole("checkbox", { name: /temperature/ }).check();
  await expect(submit).toBeEnabled();
  const id = await submittedJob(page, "/api/ml/train/jobs", () => submit.click());
  await expect(page.getByRole("region", { name: "ML 실행 결과" })).toContainText(id);
});
