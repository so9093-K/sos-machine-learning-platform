/** 액세스 토큰은 메모리에만 유지한다. 저장소나 로그에 기록하지 않는다. */
import type { components } from "./api.generated";

type Problem = components["schemas"]["ProblemOut"];
let accessToken: string | null = null;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null,
    readonly requestId: string | null,
    readonly retryPolicy: string | null,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** 오류 코드와 요청 ID. 운영 문의·로그 대조에 쓰는 참조 정보다. */
  get reference(): string | null {
    return [this.code, this.requestId ? `요청 ID: ${this.requestId}` : null]
      .filter(Boolean).join(" · ") || null;
  }

  /** 서버 재시도 정책에 따른 다음 행동 안내. */
  get guidance(): string | null {
    if (this.retryPolicy === "manual_verification_required") {
      return "작업의 실행·커밋 상태를 확인하기 전에는 동일 요청을 다시 제출하지 마세요.";
    }
    if (this.retryPolicy === "after_refresh") {
      return "현재 상태를 새로 조회한 뒤 재시도 여부를 판단하세요.";
    }
    return null;
  }

  override toString() {
    const message = this.reference ? `${this.message} (${this.reference})` : this.message;
    return this.guidance ? `${message} · ${this.guidance}` : message;
  }
}

/** 화면 오류 안내의 구성: 사용자가 읽을 메시지, 다음 행동, 운영 참조 정보. */
export type ErrorNotice = { message: string; guidance: string | null; reference: string | null };

/** 오류를 문자열로 합치지 않고 메시지·안내·참조 정보를 나눠 전달한다. */
export function describeError(error: unknown): ErrorNotice {
  if (error instanceof ApiError) {
    return { message: error.message, guidance: error.guidance, reference: error.reference };
  }
  // 화면에서 직접 만든 입력 검증 메시지는 문자열로 전달된다.
  const message = typeof error === "string" ? error : formatError(error);
  return { message, guidance: null, reference: null };
}

/** UI에서 요청 ID·오류 코드를 보존하되 임의 예외의 원문은 추가하지 않는다. */
export function formatError(error: unknown): string {
  if (error instanceof ApiError) return error.toString();
  if (error instanceof TypeError) return "네트워크 요청에 실패했습니다. 연결 상태를 확인하고 다시 시도하세요.";
  // 필드 입력 검증 등 의도적으로 발생시킨 로컬 Error 메시지는 유지한다.
  return error instanceof Error ? error.message : "요청을 처리하지 못했습니다. 다시 시도하세요.";
}

export function setAccessToken(value: string | null) {
  const next = value || null;
  if (accessToken !== next) pendingJobKeys.clear();
  accessToken = next;
}

/** 구조화된 오류에서 메시지와 요청 ID·재시도 정책을 읽는다. */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<T>;
}

export function jsonPost<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

/** fetch와 XHR에서 같은 구조화된 오류 처리를 사용한다. */
async function responseError(response: Response): Promise<ApiError> {
    let message = `요청에 실패했습니다. (HTTP ${response.status})`;
    let code: string | null = null;
    let retryPolicy: string | null = null;
    let requestId = response.headers.get("X-Request-ID");
    try {
      const body = await response.json() as Partial<Problem>;
      if (typeof body.code === "string") code = body.code;
      if (typeof body.retry_policy === "string") retryPolicy = body.retry_policy;
      if (typeof body.request_id === "string") requestId = body.request_id;
      const detail: unknown = body.detail;
      if (typeof detail === "string") message = detail;
      else if (Array.isArray(detail) && detail.length) {
        const first = detail[0] as { loc?: Array<string | number>; msg?: string };
        const field = first.loc?.filter(part => part !== "body").join(".");
        message = field ? `${field}: ${first.msg ?? "입력을 확인하세요."}`
          : (first.msg ?? message);
      }
    } catch { /* JSON이 아니어도 HTTP 상태·요청 ID를 보존한다. */ }
    // 프록시가 HTML 또는 빈 413을 반환해도 오류 코드는 서버가 정의한 경우에만 사용한다.
    if (response.status === 413 && !code) {
      message = "전송할 파일 또는 요청 본문이 서버의 크기 제한을 초과했습니다.";
    }
    return new ApiError(message, response.status, code, requestId, retryPolicy);
}

/** 전송된 바이트와 서버의 등록 완료를 구분하는 파일 업로드 전용 요청. */
export function uploadFile<T>(
  path: string,
  file: File,
  onProgress: (loaded: number, total: number) => void,
  onTransferComplete: () => void,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    if (accessToken) xhr.setRequestHeader("Authorization", `Bearer ${accessToken}`);
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) onProgress(event.loaded, event.total);
    };
    xhr.upload.onload = onTransferComplete;
    xhr.onerror = () => reject(new Error("업로드 중 네트워크 연결이 끊겼습니다. 등록 여부를 확인해 주세요."));
    xhr.onabort = () => reject(new Error("요청이 중단됐습니다. 등록 여부를 확인해 주세요."));
    xhr.onload = () => {
      const headers = new Headers();
      const requestId = xhr.getResponseHeader("X-Request-ID");
      if (requestId) headers.set("X-Request-ID", requestId);
      const response = new Response(xhr.responseText, { status: xhr.status, headers });
      if (!response.ok) {
        void responseError(response).then(reject, reject);
        return;
      }
      try {
        resolve(JSON.parse(xhr.responseText) as T);
      } catch {
        reject(new Error("서버 응답을 읽을 수 없습니다. 등록 여부를 확인해 주세요."));
      }
    };
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}

/** 응답이 유실된 제출의 키를 유지해 동일 클릭을 안전하게 재시도한다. */
const pendingJobKeys = new Map<string, string>();

export async function submitJob<T>(path: string, body: unknown): Promise<T> {
  const signature = path + ":" + JSON.stringify(body);
  let key = pendingJobKeys.get(signature);
  if (!key) {
    key = crypto.randomUUID();
    pendingJobKeys.set(signature, key);
  }
  try {
    const result = await request<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": key },
      body: JSON.stringify(body),
    });
    pendingJobKeys.delete(signature);
    return result;
  } catch (error) {
    // HTTP 5xx/408/429도 서버의 Job 접수 뒤 발생할 수 있다.
    // 확정적인 4xx만 키를 해제해 의도하지 않은 중복 Job을 방지한다.
    if (error instanceof ApiError && error.status < 500 &&
        ![408, 429].includes(error.status)) {
      pendingJobKeys.delete(signature);
    }
    throw error;
  }
}
