/** API 오류를 검증 실패와 서비스 장애로 구분해 전달한다. */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let message = `요청에 실패했습니다. (HTTP ${response.status})`;
    try {
      const body = await response.json() as { detail?: string | { code?: string; message?: string } |
        Array<{ loc?: Array<string | number>; msg?: string }> };
      const detail = body.detail;
      if (typeof detail === "string") message = detail;
      else if (Array.isArray(detail) && detail.length) {
        const first = detail[0];
        const field = first.loc?.filter(part => part !== "body").join(".");
        message = field ? `${field}: ${first.msg ?? "입력을 확인하세요."}` : (first.msg ?? message);
      } else if (detail && !Array.isArray(detail) && typeof detail.message === "string") {
        message = detail.code ? `${detail.code}: ${detail.message}` : detail.message;
      }
    } catch { /* 응답 본문이 JSON 형식이 아닐 수 있다. */ }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}
export function jsonPost<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}
