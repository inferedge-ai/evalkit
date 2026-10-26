import { defineConfig, loadEnv, searchForWorkspaceRoot } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react(), tailwindcss()],
    server: {
      // IPv4 explicitly — a reverse proxy dialing 127.0.0.1 misses vite's
      // default localhost bind when it ends up v6-only ([::1]).
      host: "127.0.0.1",
      port: 5180,
      allowedHosts: ["evalkit.local"],
      // The docs pages are ../docs/user/*.md, outside the UI root.
      fs: { allow: [searchForWorkspaceRoot(process.cwd()), "../docs/user"] },
      proxy: {
        "/api": {
          target: env.VITE_API_PROXY ?? "http://localhost:8020",
          changeOrigin: true,
        },
      },
    },
  };
});
