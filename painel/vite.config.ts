import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Em desenvolvimento o Vite serve a tela e repassa /api para o servidor
// Python (porta 8765, ou API_PORTA). Em producao o proprio servidor Python
// serve a pasta dist/.
const portaApi = process.env.API_PORTA ?? "8765";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: `http://127.0.0.1:${portaApi}`, changeOrigin: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
