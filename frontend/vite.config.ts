import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/auth": "http://localhost:8000",
      "/garments": "http://localhost:8000",
      "/uploads": "http://localhost:8000",
      "/weather": "http://localhost:8000",
      "/outfits": "http://localhost:8000",
      "/static": "http://localhost:8000"
    }
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts"
  }
});
