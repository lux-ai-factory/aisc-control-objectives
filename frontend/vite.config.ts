import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The wizard UI is its own app on its own port, separate from the catalogue.
// It talks to the wizard API (VITE_WIZARD_URL) and links back to the catalogue
// (VITE_CATALOGUE_URL).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3001,
    host: true,
  },
  build: {
    outDir: "build",
    chunkSizeWarningLimit: 1500,
  },
});
