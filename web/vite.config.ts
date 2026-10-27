import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "SOS_");
  const apiPort = Number(env.SOS_API_PORT || 9602);
  const webPort = Number(env.SOS_WEB_PORT || 9601);
  const apiTarget = env.SOS_API_INTERNAL_URL || `http://127.0.0.1:${apiPort}`;
  // 브라우저에는 로컬 운영 도구의 공개 포트만 전달한다. SOS_ 환경변수 전체를 노출하지 않는다.
  const port = (value: string | undefined, fallback: string) =>
    value && /^\d{1,5}$/.test(value) ? value : fallback;
  const operatorPorts = {
    dagster: port(env.SOS_DAGSTER_PORT, "9607"),
    mlflow: port(env.SOS_MLFLOW_PORT, "9606"),
  };
  return {
    plugins: [react()],
    define: { __SOS_OPERATOR_PORTS__: JSON.stringify(operatorPorts) },
    server: {
      host: "127.0.0.1",
      port: webPort,
      strictPort: true,
      proxy: {
        "/api": apiTarget,
        "/docs": apiTarget,
        "/openapi.json": apiTarget,
      },
    },
  };
});
