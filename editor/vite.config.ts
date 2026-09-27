import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies API + websocket to the Python backend (port 8770).
// `vite build` emits to dist/, which the backend serves in production.
// The proxy keeps the page's Host (changeOrigin: false; the string shorthand
// would turn it on), so the backend sees the address the page's Origin names
// and its same-origin check passes.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8770", changeOrigin: false },
      "/ws": { target: "ws://localhost:8770", ws: true, changeOrigin: false },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
