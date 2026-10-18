import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "SOS_");
  const apiPort = Number(env.SOS_API_PORT || 9602);
  const webPort = Number(env.SOS_WEB_PORT || 9601);
  const apiTarget = env.SOS_API_INTERNAL_URL || `http://127.0.0.1:${apiPort}`;
  return {
    plugins: [react()],
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
