import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Multi-page: index.html = marketing landing (served at / in prod), app.html =
// the React tool (served at /app via vercel.json rewrites). The React dev server
// runs on :5173 and talks to the FastAPI backend (VITE_API_BASE, default :8000).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: true },
  build: {
    rollupOptions: {
      input: {
        main: "index.html", // landing
        app: "app.html",    // React app
      },
    },
  },
});
