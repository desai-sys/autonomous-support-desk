import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// In development, `npm run dev` proxies the API to a local console server
// (uvicorn console.server:app --port 8090; the local agent uses 8080).
const backend = "http://localhost:8090";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { "/api": backend, "/auth": backend, "/oauth2callback": backend },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 900 },
});
