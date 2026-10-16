import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, formatError, request, setAccessToken, submitJob } from "./api";

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
      expect(formatError(diagnosed)).toContain("req-example");
      expect(formatError(diagnosed)).toContain("SOS-ICEBERG-UNCERTAIN");
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


describe("ambiguous Job submission response", () => {
  it("reuses the same submission identity after HTTP 503", async () => {
    const sent: Array<string | null> = [];
    let call = 0;
    vi.stubGlobal("crypto", { randomUUID: () => "test-key-stable" });
    vi.stubGlobal("fetch", vi.fn(async (_path: string, init: RequestInit) => {
      sent.push(new Headers(init.headers).get("Idempotency-Key"));
      call += 1;
      if (call === 1) return new Response(JSON.stringify({ detail: "Temporary failure" }),
        { status: 503 });
      return new Response(JSON.stringify({ id: "same-job" }), { status: 202 });
    }));
    await expect(submitJob("/api/jobs", { dataset_id: "first" }))
      .rejects.toMatchObject({ status: 503 });
    await expect(submitJob("/api/jobs", { dataset_id: "first" }))
      .resolves.toEqual({ id: "same-job" });
    expect(sent).toEqual(["test-key-stable", "test-key-stable"]);
  });

  it("resets pending submission keys when the access token changes", async () => {
    let sequence = 0;
    const sent: Array<string | null> = [];
    vi.stubGlobal("crypto", { randomUUID: () => `actor-key-${++sequence}` });
    vi.stubGlobal("fetch", vi.fn(async (_path: string, init: RequestInit) => {
      sent.push(new Headers(init.headers).get("Idempotency-Key"));
      if (sent.length === 1) throw new TypeError("Network failed");
      return new Response(JSON.stringify({ id: "new-actor" }), { status: 202 });
    }));
    setAccessToken("actor-one");
    await expect(submitJob("/api/jobs", { dataset_id: "second" })).rejects.toThrow();
    setAccessToken("actor-two");
    await expect(submitJob("/api/jobs", { dataset_id: "second" }))
      .resolves.toEqual({ id: "new-actor" });
    expect(sent).toEqual(["actor-key-1", "actor-key-2"]);
  });

  it("uses a new key after a definitive validation rejection", async () => {
    let sequence = 0;
    const sent: Array<string | null> = [];
    vi.stubGlobal("crypto", { randomUUID: () => `validation-key-${++sequence}` });
    vi.stubGlobal("fetch", vi.fn(async (_path: string, init: RequestInit) => {
      sent.push(new Headers(init.headers).get("Idempotency-Key"));
      if (sent.length === 1) return new Response(JSON.stringify({ detail: "Invalid" }),
        { status: 422 });
      return new Response(JSON.stringify({ id: "accepted" }), { status: 202 });
    }));
    await expect(submitJob("/api/jobs", { dataset_id: "third" }))
      .rejects.toMatchObject({ status: 422 });
    await expect(submitJob("/api/jobs", { dataset_id: "third" }))
      .resolves.toEqual({ id: "accepted" });
    expect(sent).toEqual(["validation-key-1", "validation-key-2"]);
  });
});
