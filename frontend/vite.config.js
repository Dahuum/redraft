import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-only: mirror the Vercel rewrite `/app/(.*)` → `/app.html`, so refreshing a
// deep client route (e.g. /app/editor/xyz) serves the React app instead of
// falling back to the landing page. Production is handled by vercel.json.
function appRewrite() {
  return {
    name: "app-spa-rewrite",
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        const url = (req.url || "").split("?")[0];
        if (url === "/app" || url.startsWith("/app/")) {
          req.url = "/app.html";
        } else if (url === "/new") {
          req.url = "/new.html";
        }
        next();
      });
    },
  };
}

// Multi-page: index.html = marketing landing (served at / in prod), app.html =
// the React tool (served at /app via vercel.json rewrites). The React dev server
// runs on :5173 and talks to the FastAPI backend (VITE_API_BASE, default :8000).
export default defineConfig({
  plugins: [react(), appRewrite()],
  server: { port: 5173, host: true },
  build: {
    rollupOptions: {
      input: {
        main: "index.html", // landing
        app: "app.html",    // React app
        new: "new.html",    // public text→PDF compose page (guest)
      },
    },
  },
});
