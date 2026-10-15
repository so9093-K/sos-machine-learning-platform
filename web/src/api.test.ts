import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, request, setAccessToken } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

describe("API failure diagnostic contract", () => {
  it("retains status, stable error code, request ID and retry policy", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({
        detail: "보호된 Snapshot을 확인하세요.",
        code: "SOS-ICEBERG-UNCERTAIN",
        retry_policy: "manual_verification_required",
        request_id: "req-example",
      }),
      { status: 409, headers: { "Content-Type": "application/json", "X-Request-ID": "req-example" } },
    )));
    try {
      await request("/api/jobs");
      throw new Error("Expected API error");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      const diagnosed = error as ApiError;
      expect(diagnosed.code).toBe("SOS-ICEBERG-UNCERTAIN");
      expect(diagnosed.status).toBe(409);
      expect(diagnosed.requestId).toBe("req-example");
      expect(diagnosed.retryPolicy).toBe("manual_verification_required");
      expect(diagnosed.toString()).toContain("req-example");
    }
  });

  it("preserves legacy field validation and request header on non-JSON response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("temporarily unavailable", {
      status: 503, headers: { "X-Request-ID": "trace-example" },
    })));
    await expect(request("/api/jobs")).rejects.toMatchObject({
      status: 503, requestId: "trace-example", code: null,
    });
  });

  it("does not include Bearer credentials in serialized errors", async () => {
    setAccessToken("this-token-must-never-be-in-the-error");
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "인증 실패", code: "SOS-AUTH-INVALID" }),
      { status: 401 },
    ));
    vi.stubGlobal("fetch", fetchMock);
    await expect(request("/api/auth/me")).rejects.toThrow("인증 실패");
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/auth/me");
    expect(new Headers(init.headers).has("Authorization")).toBe(true);
  });
});
