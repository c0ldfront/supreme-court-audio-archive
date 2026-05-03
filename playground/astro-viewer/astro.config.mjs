import { defineConfig } from "astro/config";

export default defineConfig({
  output: "static",
  site: "https://example.github.io",
  base: process.env.ASTRO_BASE || "/",
});
