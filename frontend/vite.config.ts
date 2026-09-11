import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// The browser only ever talks to this dev server. Everything under /api is
// handed to the gateway, which keeps the app on a single origin and takes
// CORS out of the picture entirely.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const gateway = env.VITE_GATEWAY_URL ?? "http://localhost:8000";

  return {
    plugins: [react()],
    server: {
      port: Number(env.FRONTEND_PORT ?? 5173),
      proxy: {
        "/api": {
          target: gateway,
          changeOrigin: true,
          // The gateway serves /companies, not /api/companies.
          rewrite: (path) => path.replace(/^\/api/, ""),
        },
      },
    },
    build: { target: "es2022" },
  };
});
