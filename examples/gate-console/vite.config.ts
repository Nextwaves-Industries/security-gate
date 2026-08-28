import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// The gate service exposes no CORS headers (it is meant to sit on a private
// VLAN behind an operator tool). During development Vite proxies the REST
// paths to the gate so the browser only ever talks to one origin.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const gate = env.VITE_GATE_URL || "https://127.0.0.1:8443";
  const proxy = { target: gate, changeOrigin: true, secure: false };
  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: { "/api": proxy, "/healthz": proxy, "/readyz": proxy },
    },
    preview: { port: 4173, proxy: { "/api": proxy, "/healthz": proxy, "/readyz": proxy } },
  };
});
