import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The app talks to the FastAPI backend directly via VITE_API_BASE_URL
    // (see src/App.jsx / .env.example) rather than a dev-server proxy, so
    // no proxy config is required here.
  },
});
