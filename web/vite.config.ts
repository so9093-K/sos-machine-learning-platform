import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "SOS_");
  const apiPort = Number(env.SOS_API_PORT || 9602);
  const webPort = Number(env.SOS_WEB_PORT || 9601);
  return {
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: webPort,
    strictPort: true,
    proxy: {
      "/api": `http://127.0.0.1:${apiPort}`,
      "/docs": `http://127.0.0.1:${apiPort}`,
      "/openapi.json": `http://127.0.0.1:${apiPort}`,
    },
  },
  };
});
