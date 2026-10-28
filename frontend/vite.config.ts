/*
 vite.config.ts - build settings for the screen
 `npm run dev`   serves the app on :5173 and forwards /api to the Python server on :8000 (live reload while editing)
 `npm run build` writes frontend/dist, which the Python server (api/app.py) serves itself - no Node needed to run the app
*/
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { outDir: "dist", emptyOutDir: true },
});
