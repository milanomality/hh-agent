import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev: SPA на :5173, /api проксируется на uvicorn :8000 — тот же origin,
// сессионная кука ходит без CORS. В бою dist отдаётся самим FastAPI.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  build: { outDir: "dist" },
});
