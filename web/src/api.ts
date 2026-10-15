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

  override toString() {
    const reference = [this.code, this.requestId ? `요청 ID: ${this.requestId}` : null]
      .filter(Boolean).join(" · ");
    return reference ? `${this.message} (${reference})` : this.message;
  }
}

export function setAccessToken(value: string | null) {
  accessToken = value || null;
}

/** 기존 detail을 읽으면서 오류 코드와 요청 ID·재시도 정책을 별도로 유지한다. */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
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
      } else if (detail && typeof detail === "object" && !Array.isArray(detail)) {
        const legacy = detail as { code?: string; message?: string };
        if (typeof legacy.message === "string") message = legacy.message;
        if (!code && typeof legacy.code === "string") code = legacy.code;
      }
    } catch { /* JSON이 아니어도 HTTP 상태·요청 ID를 보존한다. */ }
    throw new ApiError(message, response.status, code, requestId, retryPolicy);
  }
  return response.json() as Promise<T>;
}

export function jsonPost<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}
