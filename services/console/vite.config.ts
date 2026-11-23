import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  base: "/console/",
  plugins: [vue()],
  build: {
    outDir: "dist",
  },
  server: {
    proxy: {
      "/internal": {
        target: "http://127.0.0.1:7800",
        changeOrigin: true,
      },
    },
  },
});
