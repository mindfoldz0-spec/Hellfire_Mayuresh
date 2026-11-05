#!/usr/bin/env node
/**
 * Frontend static checks (no build step, no dependencies).
 *
 *  1. every `.js` under frontend/ parses as an ES module
 *  2. every named import resolves to a real export in the target module
 *  3. every getElementById() in each app's dom.js exists in that app's HTML
 *  4. no element id is duplicated
 *  5. every stylesheet in frontend/**\/css is linked from an HTML file
 *  6. no inline <script> or on*= handler that the CSP would block
 *
 * Exits non-zero on any problem so CI fails loudly.
 */
import { readdirSync, readFileSync, statSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const frontend = path.join(root, "frontend");

function walk(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else out.push(full);
  }
  return out;
}

const allFiles = walk(frontend);
const jsFiles = allFiles.filter((f) => f.endsWith(".js"));
const htmlFiles = allFiles.filter((f) => f.endsWith(".html"));
const cssFiles = allFiles.filter((f) => f.endsWith(".css"));

let problems = 0;
const fail = (message) => {
  problems += 1;
  console.error(`FAIL ${message}`);
};

// 1. ES module syntax
const scratch = mkdtempSync(path.join(tmpdir(), "cers-check-"));
for (const file of jsFiles) {
  const copy = path.join(scratch, `${path.basename(file, ".js")}.mjs`);
  writeFileSync(copy, readFileSync(file));
  const result = spawnSync(process.execPath, ["--check", copy], { stdio: "ignore" });
  if (result.status !== 0) fail(`syntax error in ${path.relative(root, file)}`);
}
rmSync(scratch, { recursive: true, force: true });

// 2. named imports resolve
const exportsByFile = new Map();
for (const file of jsFiles) {
  const source = readFileSync(file, "utf8");
  const names = new Set();
  for (const m of source.matchAll(/export\s+(?:async\s+)?(?:function|const|let|var)\s+([A-Za-z0-9_$]+)/g)) {
    names.add(m[1]);
  }
  exportsByFile.set(path.resolve(file), names);
}
for (const file of jsFiles) {
  const source = readFileSync(file, "utf8");
  for (const m of source.matchAll(/import\s*\{([^}]*)\}\s*from\s*["'](\.[^"']+)["']/g)) {
    const target = path.resolve(path.dirname(file), m[2]);
    const exported = exportsByFile.get(target);
    if (!exported) {
      fail(`${path.relative(root, file)} imports missing module ${m[2]}`);
      continue;
    }
    for (const raw of m[1].split(",")) {
      const name = raw.trim().split(/\s+as\s+/)[0].trim();
      if (name && !exported.has(name)) {
        fail(`${path.relative(root, file)} imports {${name}} which ${m[2]} does not export`);
      }
    }
  }
}

// 3 + 4. DOM bindings and duplicate ids
for (const app of ["caller", "officer"]) {
  const htmlPath = path.join(frontend, app, "index.html");
  const domPath = path.join(frontend, app, "js", "dom.js");
  const html = readFileSync(htmlPath, "utf8");
  const dom = readFileSync(domPath, "utf8");
  const ids = [...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]);
  const seen = new Set();
  for (const id of ids) {
    if (seen.has(id)) fail(`duplicate id "${id}" in ${app}/index.html`);
    seen.add(id);
  }
  const refs = [...dom.matchAll(/getElementById\("([^"]+)"\)/g)].map((m) => m[1]);
  for (const ref of new Set(refs)) {
    if (!seen.has(ref)) fail(`${app}/js/dom.js references #${ref} which is not in the HTML`);
  }
}

// 5. stylesheet linkage
const linked = new Set();
for (const file of htmlFiles) {
  const html = readFileSync(file, "utf8");
  for (const m of html.matchAll(/(?:href|src)="\/(?:caller|officer)\/(?:css|js)\/([^"?]+)/g)) {
    linked.add(path.basename(m[1]));
  }
}
for (const file of cssFiles) {
  if (!linked.has(path.basename(file))) {
    fail(`${path.relative(root, file)} is not linked from any HTML file`);
  }
}

// 6. CSP-hostile markup
for (const file of htmlFiles) {
  const html = readFileSync(file, "utf8");
  if (/<script(?![^>]*\bsrc=)/.test(html)) {
    fail(`inline <script> in ${path.relative(root, file)} (blocked by CSP)`);
  }
  if (/\son(?:click|error|load|change)=/.test(html)) {
    fail(`inline event handler in ${path.relative(root, file)} (blocked by CSP)`);
  }
}

if (problems) {
  console.error(`\n${problems} frontend problem(s).`);
  process.exit(1);
}
console.log(
  `Frontend OK: ${jsFiles.length} modules, ${htmlFiles.length} pages, ${cssFiles.length} stylesheets.`
);
