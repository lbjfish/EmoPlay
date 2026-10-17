import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const dependencyChunk = (moduleId: string): string | undefined => {
  const id = moduleId.replaceAll("\\", "/");
  if (id.includes("vite/preload-helper")) {
    return "react-core";
  }
  if (id.endsWith("/src/enhancements/effects.tsx")) {
    return "effects";
  }
  if (id.endsWith("/src/enhancements/mediaReview.ts")) {
    return "media-review";
  }
  if (id.endsWith("/src/enhancements/godCharts.tsx")) {
    return "god-charts";
  }
  if (!id.includes("/node_modules/")) {
    return undefined;
  }
  if (
    id.includes("/node_modules/react/") ||
    id.includes("/node_modules/react-dom/") ||
    id.includes("/node_modules/scheduler/")
  ) {
    return "react-core";
  }
  if (
    id.includes("/node_modules/motion/") ||
    id.includes("/node_modules/@tsparticles/")
  ) {
    return "effects";
  }
  if (
    id.includes("/node_modules/photoswipe/") ||
    id.includes("/node_modules/wavesurfer.js/")
  ) {
    return "media-review";
  }
  if (id.includes("/node_modules/recharts/") || id.includes("/node_modules/victory-vendor/")) {
    return "god-charts";
  }
  return undefined;
};

export default defineConfig({
  base: "/assets/",
  plugins: [react()],
  build: {
    outDir: resolve(import.meta.dirname, "dist"),
    emptyOutDir: true,
    sourcemap: false,
    cssCodeSplit: false,
    assetsInlineLimit: 0,
    minify: "esbuild",
    reportCompressedSize: false,
    modulePreload: false,
    rollupOptions: {
      output: {
        entryFileNames: "game.js",
        chunkFileNames: "[name].js",
        assetFileNames: (asset) => (asset.name?.endsWith(".css") ? "game.css" : "[name][extname]"),
        manualChunks: dependencyChunk,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: true,
  },
});
