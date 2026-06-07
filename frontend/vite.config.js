import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The React dev server runs on :5173 and talks to the FastAPI backend on :8000.
// CORS is open on the backend for dev, and VITE_API_BASE can override the target.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
  },
});
