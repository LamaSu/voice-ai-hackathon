import { defineConfig } from "vite";

// The bot's SmallWebRTC signalling lives on the Pipecat runner (default :7860).
// Proxying keeps the browser on one origin, so getUserMedia stays happy without
// a second TLS certificate during local development.
const BOT = process.env.BOT_URL || "http://127.0.0.1:7860";

export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/api": { target: BOT, changeOrigin: true },
    },
  },
});
