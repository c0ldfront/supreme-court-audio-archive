import { defineConfig } from "astro/config";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  output: "static",
  site: "https://example.github.io",
  base: process.env.ASTRO_BASE || "/",
  outDir: process.env.SCOTUS_OUT_DIR ? resolve(projectRoot, process.env.SCOTUS_OUT_DIR) : undefined,
  publicDir: process.env.SCOTUS_PUBLIC_DIR ? resolve(projectRoot, process.env.SCOTUS_PUBLIC_DIR) : undefined,
});
