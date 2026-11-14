import { svelte } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [svelte(), tailwindcss()],
  resolve: {
    alias: {
      // `shadcn-svelte` resolves `aliases.components` through this, so a block's
      // alias-relative target (`nb/AppShell.svelte`) lands in src/lib/components.
      $lib: "/src/lib",
    },
  },
  test: {
    passWithNoTests: true,
    reporter: ["default"],
    coverage: {
      reporter: ["lcov", "text"],
      thresholds: {
        statements: 80,
        branches: 50,
        functions: 80,
        lines: 80,
      },
    },
  },
});
