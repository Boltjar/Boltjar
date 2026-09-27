import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies API + websocket to the Python backend (port 8770).
// `vite build` emits to dist/, which the backend serves in production.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8770",
      "/ws": { target: "ws://localhost:8770", ws: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
