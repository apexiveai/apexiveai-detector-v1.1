import { defineConfig, loadEnv } from "vite";

import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "VITE_");
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8020";

  return {
    plugins: [react()],

    server: {

      host: "127.0.0.1",

      port: 5173,

      proxy: {
        "/api": {
          target: apiProxyTarget,
          changeOrigin: true,
        },
        "/health": {
          target: apiProxyTarget,
          changeOrigin: true,
        },
      },

    },

    preview: {

      host: "0.0.0.0",

      port: 4173,

    },

  };
});