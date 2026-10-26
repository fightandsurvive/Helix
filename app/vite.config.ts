import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri requires a fixed port and no clearScreen so the CLI can
// manage the terminal output during dev.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    watch: { ignored: ["**/src-tauri/**"] },
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: { target: "es2022", minify: "esbuild", sourcemap: false },
});
