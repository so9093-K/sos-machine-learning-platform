/** 실제 SOS Compose 서비스에 연결하여 브라우저의 첫 Pipeline 여정을 검증한다.
 * Chrome DevTools Protocol만 사용하므로 프로덕션 Web Runtime에 테스트 의존성을 추가하지 않는다.
 */
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const token = process.env.SOS_E2E_TOKEN;
assert(token, "격리된 CI에서 발급한 SOS_E2E_TOKEN이 필요합니다.");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const browser = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
  .find(binary => spawnSync("which", [binary], { stdio: "ignore" }).status === 0);
assert(browser, "실제 브라우저 검증을 위한 Chrome/Chromium을 찾을 수 없습니다.");

const profile = await mkdtemp(path.join(os.tmpdir(), "sos-browser-e2e-"));
const port = 19274;
const chrome = spawn(browser, [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
  "--no-first-run", "--no-default-browser-check", "--remote-allow-origins=*",
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  "about:blank",
], { stdio: "ignore" });
let socket = null;
let nextId = 0;
const waiting = new Map();

async function until(predicate, label, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    try { if (await predicate()) return; }
    catch (error) { last = error; }
    await sleep(600);
  }
  throw new Error(`시간 초과: ${label}${last ? " · " + last.message : ""}`);
}
async function command(method, params = {}) {
  const id = ++nextId;
  const promise = new Promise((resolve, reject) => {
    waiting.set(id, { resolve, reject });
  });
  socket.send(JSON.stringify({ id, method, params }));
  return promise;
}
async function evaluate(expression) {
  const result = await command("Runtime.evaluate", {
    expression, returnByValue: true, awaitPromise: true, userGesture: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text ?? "브라우저 JavaScript 평가 실패");
  }
  return result.result?.value;
}
function buttonClick(label, index = 0) {
  return evaluate(`(() => {
    const buttons = [...document.querySelectorAll("button")]
      .filter(button => button.textContent?.includes(${JSON.stringify(label)}));
    const button = buttons[${index}];
    if (!button || button.disabled) return false;
    button.click();
    return true;
  })()`);
}
const hasText = label => evaluate(`document.body.innerText.includes(${JSON.stringify(label)})`);
const artifactsDir = path.resolve("artifacts");

try {
  let meta;
  await until(async () => {
    const response = await fetch(`http://127.0.0.1:${port}/json/version`);
    if (!response.ok) return false;
    meta = await response.json();
    return Boolean(meta.webSocketDebuggerUrl);
  }, "Chrome 디버거 시작", 30000);

  const response = await fetch(
    `http://127.0.0.1:${port}/json/new?http://127.0.0.1:9601/`, { method: "PUT" });
  assert(response.ok, "브라우저 탭 생성 실패");
  const target = await response.json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  socket.addEventListener("message", event => {
    const message = JSON.parse(String(event.data));
    const pending = waiting.get(message.id);
    if (!pending) return;
    waiting.delete(message.id);
    if (message.error) pending.reject(new Error(message.error.message));
    else pending.resolve(message.result);
  });
  await command("Page.enable");
  await command("Runtime.enable");
  await command("Page.navigate", { url: "http://127.0.0.1:9601/" });

  await until(() => hasText("SOS Platform 접근 인증"), "인증 화면", 40000);
  assert(await evaluate(`(() => {
    const input = document.querySelector('input[aria-label="액세스 토큰"]');
    if (!input) return false;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(input, ${JSON.stringify(token)});
    input.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  })()`), "토큰 입력 실패");
  await until(() => buttonClick("인증"), "인증 제출 버튼 활성화", 10000);
  await until(() => hasText("무엇을 시작할까요?"), "홈 화면", 40000);
  assert(await buttonClick("활동 이상 탐지 사용하기"), "홈의 Pipeline 진입 실패");

  await until(() => hasText("이 분석에서 사용할 수 있는 기능"), "Pipeline 기능 발견성");
  await until(async () => (await evaluate(`[...document.querySelectorAll("button")].some(
    button => button.textContent.includes("예제 데이터 선택하고 입력 확인") && !button.disabled)`)),
  "선택 가능한 IF 예제");
  assert(await buttonClick("예제 데이터 선택하고 입력 확인"), "예제 선택 실패");

  await until(() => hasText("입력 구조 확인 완료"), "예제 입력 적합성 확인", 60000);
  assert(await buttonClick("이 데이터로 분석 실행하기"), "Pipeline 실행 제출 실패");
  let terminalPipeline = null;
  await until(async () => {
    const state = await evaluate(`(() => ({
      status: document.querySelector(".pipeline-context-facts strong")?.textContent?.trim() ?? "",
      error: document.querySelector(".pipeline-run-failure-code")?.textContent?.trim() ?? "",
      complete: Boolean(document.getElementById("pipeline-analysis-first-heading")),
    }))()`);
    if (/\b(Failed|Cancelled)\b/.test(state.status)) {
      terminalPipeline = state;
      return true;
    }
    return state.complete;
  }, "Pipeline Worker 학습·보정·Test 완료", 420000);
  if (terminalPipeline) {
    throw new Error(`Pipeline ${terminalPipeline.status}${
      terminalPipeline.error ? " · " + terminalPipeline.error : ""}`);
  }
  await until(() => hasText("생성한 모델을 다른 데이터에 적용하기"),
    "생성 모델 활용 영역", 60000);
  for (const [i, title] of [
    [0, "Training — 무엇을 학습했는가"],
    [1, "Calibration — 기준은 어떻게 정했는가"],
    [2, "Test — 적용 후 무엇이 달라졌는가"],
  ]) {
    assert(await evaluate(`(() => {
      const button = [...document.querySelectorAll(".pipeline-capabilities button")]
        .filter(item => item.textContent.includes("결과 살펴보기"))[${i}];
      if (!button) return false;
      button.click();
      return true;
    })()`), `${title} 열기 실패`);
    await until(() => hasText(title), title, 30000);
  }
  await until(() => evaluate(`Boolean(document.querySelector('select[aria-label="Scoring Dataset"]'))`),
    "모델 Scoring 입력", 60000);
  assert(await evaluate(`(() => {
    const select = document.querySelector('select[aria-label="Scoring Dataset"]');
    const option = [...select.options].find(item => item.value);
    if (!option) return false;
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set;
    setter.call(select, option.value);
    select.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  })()`), "호환 Dataset Scoring 선택 실패");
  await until(async () => (await evaluate(`[...document.querySelectorAll("button")].some(
    button => button.textContent.includes("Scoring Job 제출") && !button.disabled)`)),
  "Scoring 제출 준비", 15000);
  assert(await buttonClick("Scoring Job 제출"), "독립 Scoring Job 제출 실패");
  await until(() => hasText("Scoring Job · Succeeded"), "재사용 모델의 Scoring 완료", 180000);
  console.log("실제 브라우저 → 홈 → IF 예제 → Worker 분석 → Training/Calibration/Test → 모델 Scoring: 통과");
} catch (error) {
  console.error("브라우저 E2E 실패:", error.message);
  try {
    if (socket?.readyState === WebSocket.OPEN) {
      const screenshot = await command("Page.captureScreenshot", { format: "png" });
      await mkdir(artifactsDir, { recursive: true });
      await writeFile(path.join(artifactsDir, "pipeline-e2e-failure.png"),
        Buffer.from(screenshot.data, "base64"));
    }
  } catch (captureError) {
    console.error("실패 화면 수집도 실패:", captureError.message);
  }
  process.exitCode = 1;
} finally {
  socket?.close();
  chrome.kill("SIGTERM");
  await rm(profile, { recursive: true, force: true }).catch(() => {});
}
