import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiPort = Number(process.env.SOS_API_PORT || 9602);
const webPort = Number(process.env.SOS_WEB_PORT || 9601);

export default defineConfig({
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
});
