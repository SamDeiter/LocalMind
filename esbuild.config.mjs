/**
 * esbuild configuration for LocalMind frontend.
 *
 * Usage:
 *   npm run build:frontend          # one-shot production build
 *   npm run dev:frontend             # watch mode for development
 *
 * Output:  frontend/dist/bundle.js  (+ sourcemap)
 *
 * External globals (loaded via CDN <script> tags in index.html):
 *   - marked, hljs          — Markdown & syntax highlighting
 *   - require, monaco       — Monaco editor AMD loader
 *   - tailwind              — Tailwind CSS CDN
 */

import * as esbuild from "esbuild";
import { existsSync, mkdirSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const isWatch = process.argv.includes("--watch");

// Ensure output directory exists
const outdir = resolve(__dirname, "frontend/dist");
if (!existsSync(outdir)) {
  mkdirSync(outdir, { recursive: true });
}

/** @type {import('esbuild').BuildOptions} */
const buildOptions = {
  entryPoints: [resolve(__dirname, "frontend/app.js")],
  bundle: true,
  outfile: resolve(__dirname, "frontend/dist/bundle.js"),
  format: "esm",
  platform: "browser",
  target: ["es2020"],
  sourcemap: true,
  minify: !isWatch,
  metafile: true,

  // CDN globals (marked, hljs, monaco, require) are referenced as bare
  // identifiers in module code.  esbuild in ESM mode leaves global references
  // untouched and only resolves ES `import` specifiers, so no `external`
  // config is needed for them.
  //
  // The AMD-style `require(["vs/editor/editor.main"], cb)` in editor.js is
  // a runtime call to the Monaco loader (loaded via CDN <script> tag) — it
  // uses an array argument, not a string, so esbuild does not treat it as a
  // CommonJS require and will not try to resolve or bundle it.

  // Log level
  logLevel: "info",
};

async function build() {
  if (isWatch) {
    const ctx = await esbuild.context(buildOptions);
    await ctx.watch();
    console.log("[esbuild] Watching for changes...");
    // Keep process alive
  } else {
    const result = await esbuild.build(buildOptions);

    // Print bundle size summary
    if (result.metafile) {
      const text = await esbuild.analyzeMetafile(result.metafile, {
        verbose: false,
      });
      console.log(text);
    }
  }
}

build().catch((err) => {
  console.error(err);
  process.exit(1);
});
