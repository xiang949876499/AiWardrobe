import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/auth": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/garments": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "^/uploads(?:/|$)": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/weather": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/outfits": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/purchase": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/shopping": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/static": { target: "http://127.0.0.1:8000", changeOrigin: true }
    }
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts"
  }
});
