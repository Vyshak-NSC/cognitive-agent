import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The browser talks to /api; Vite forwards it to the Python API, so no CORS setup is needed.
// Run the API with `uv run cpa-api`. Set CPA_API_URL if it is not on 127.0.0.1:8000.
const api = {
  "/api": {
    target: process.env.CPA_API_URL || "http://127.0.0.1:8000",
    changeOrigin: true,
    rewrite: (path) => path.replace(/^\/api/, ""),
  },
};

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: api },
  preview: { port: 5173, proxy: api },
  test: { environment: "jsdom", setupFiles: "./src/test-setup.js", globals: true },
});
