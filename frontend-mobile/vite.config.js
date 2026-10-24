import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1",
        changeOrigin: true,
        configure(proxy) {
          // The browser talks to Vite on the same origin. Match the upstream
          // Origin to its rewritten Host when forwarding to the local API.
          proxy.on("proxyReq", (proxyReq, req) => {
            if (req.headers.origin) {
              proxyReq.setHeader("Origin", "http://127.0.0.1");
            }
          });
        },
      },
    },
  },
  test: { environment: "node", env: { VITE_USE_MOCK: "true" } },
});
