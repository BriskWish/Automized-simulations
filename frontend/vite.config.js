import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backendPort = env.WILLY_SERVER_PORT || "7860";

  return {
    plugins: [react()],
    build: { outDir: "dist", emptyOutDir: true },
    server: { port: 5174, proxy: { "/api": `http://127.0.0.1:${backendPort}` } },
  };
});
