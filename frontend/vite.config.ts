import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const backendTarget = process.env.VITE_BACKEND_TARGET ?? "http://127.0.0.1:8031";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true,
    proxy: {
      "/auth": { target: backendTarget, changeOrigin: true },
      "/garments": { target: backendTarget, changeOrigin: true },
      "^/uploads(?:/|$)": { target: backendTarget, changeOrigin: true },
      "/weather": { target: backendTarget, changeOrigin: true },
      "/outfits": { target: backendTarget, changeOrigin: true },
      "/purchase": { target: backendTarget, changeOrigin: true },
      "/shopping": { target: backendTarget, changeOrigin: true },
      "/reports": { target: backendTarget, changeOrigin: true },
      "/preferences": { target: backendTarget, changeOrigin: true },
      "/static": { target: backendTarget, changeOrigin: true }
    }
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts"
  }
});
