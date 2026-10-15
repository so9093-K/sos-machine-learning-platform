import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, setAccessToken, uploadFile } from "./api";

class FakeUploadRequest {
  static current: FakeUploadRequest;
  readonly upload: {
    onprogress?: (event: { lengthComputable: boolean; loaded: number; total: number }) => void;
    onload?: () => void;
  } = {};
  onload?: () => void;
  onerror?: () => void;
  onabort?: () => void;
  responseText = "";
  status = 0;
  headers = new Map<string, string>();
  sent: FormData | null = null;

  constructor() { FakeUploadRequest.current = this; }
  open(_method: string, _path: string) {}
  setRequestHeader(key: string, value: string) { this.headers.set(key, value); }
  getResponseHeader(key: string) {
    return key === "X-Request-ID" ? "req-upload-1" : null;
  }
  send(data: FormData) { this.sent = data; }
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

describe("file upload API contract", () => {
  it("distinguishes byte transfer completion from the server's 201 response", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeUploadRequest);
    setAccessToken("secret-in-memory");
    const progress = vi.fn();
    const transferFinished = vi.fn();
    const file = new File(["signal\\n1\\n"], "reading.csv", { type: "text/csv" });
    const result = uploadFile<{ id: string }>(
      "/api/datasets/files", file, progress, transferFinished,
    );
    const xhr = FakeUploadRequest.current;
    expect(xhr.headers.get("Authorization")).toBe("Bearer secret-in-memory");
    expect(xhr.sent?.get("file")).toBe(file);
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 3, total: file.size });
    xhr.upload.onload?.();
    expect(progress).toHaveBeenCalledWith(3, file.size);
    expect(transferFinished).toHaveBeenCalledOnce();
    xhr.status = 201;
    xhr.responseText = JSON.stringify({ id: "dataset-created" });
    xhr.onload?.();
    await expect(result).resolves.toEqual({ id: "dataset-created" });
  });

  it("preserves the server's structured 413 error and request ID", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeUploadRequest);
    const result = uploadFile("/api/datasets/files", new File(["x"], "reading.csv"),
      () => {}, () => {});
    const xhr = FakeUploadRequest.current;
    xhr.status = 413;
    xhr.responseText = JSON.stringify({
      detail: "파일 크기 초과", code: "SOS-UPLOAD-TOO-LARGE",
      request_id: "req-upload-1", retry_policy: "never",
    });
    xhr.onload?.();
    await expect(result).rejects.toMatchObject({
      status: 413, code: "SOS-UPLOAD-TOO-LARGE",
      requestId: "req-upload-1", retryPolicy: "never",
    } satisfies Partial<ApiError>);
  });

  it("explains an HTML proxy 413 without inventing a server error code", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeUploadRequest);
    const result = uploadFile("/api/datasets/files", new File(["x"], "reading.csv"),
      () => {}, () => {});
    const xhr = FakeUploadRequest.current;
    xhr.status = 413;
    xhr.responseText = "<html>Too Large</html>";
    xhr.onload?.();
    await expect(result).rejects.toMatchObject({
      status: 413, code: null, requestId: "req-upload-1",
    } satisfies Partial<ApiError>);
  });
});
