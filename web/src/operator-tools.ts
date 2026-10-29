/** 같은 PC의 로컬 운영 도구(Dagster·MLflow·Feast) 주소. SOS 권한을 대신하지 않는다. */

// Vite 설정이 Compose의 공개 포트만 주입한다. 설정 없이 모듈을 불러오는 검증 환경에서는 기본 포트를 쓴다.
declare const __SOS_OPERATOR_PORTS__: { dagster: string; mlflow: string; feast: string } | undefined;
const DEFAULT_PORTS = { dagster: "9607", mlflow: "9606", feast: "9608" };

function ports() {
  return typeof __SOS_OPERATOR_PORTS__ === "undefined" ? DEFAULT_PORTS : __SOS_OPERATOR_PORTS__;
}

function origin(port: string): string {
  return `${window.location.protocol}//${window.location.hostname}:${port}`;
}

/** 운영 UI는 localhost에만 공개되므로 같은 PC에서 접속한 경우에만 링크를 제공한다. */
export function isLocalOperatorHost(): boolean {
  return ["localhost", "127.0.0.1", "::1", "[::1]"].includes(window.location.hostname);
}

export function dagsterUrl(): string {
  return origin(ports().dagster);
}

export function mlflowUrl(): string {
  return origin(ports().mlflow);
}

export function mlflowRunUrl(runId: string): string {
  return `${mlflowUrl()}/#/runs/${encodeURIComponent(runId)}`;
}

export function feastUrl(): string {
  return origin(ports().feast);
}
