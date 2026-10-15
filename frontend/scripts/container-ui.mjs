// Post-build step for the one-container UI (REDRAFT_UI=container vite build, then this).
//
// The container runs on a customer's network, possibly with no route out. dist/app.html must
// not name another host: no analytics, and the Google fonts are downloaded NOW (build time,
// network allowed) and served from the container itself.
//
// Best effort on the fonts: if the download fails, the page still loads in the system fonts.
// It never leaves a link to Google behind: a stylesheet that cannot be reached blocks painting.
import { createHash } from "node:crypto";
import { mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";

const dist = process.argv[2] || "dist";
const page = `${dist}/app.html`;
// A modern browser's UA is what makes Google answer with woff2 files.
const UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";

let html = await readFile(page, "utf8");

const sheets = [...html.matchAll(/<link[^>]*href="(https:\/\/fonts\.googleapis\.com\/[^"]+)"[^>]*>/g)]
  .map((m) => m[1].replaceAll("&amp;", "&"));

html = html
  .replace(/<link[^>]*(?:fonts\.googleapis\.com|fonts\.gstatic\.com)[^>]*>\s*/g, "")
  .replace(/<!--[^>]*PostHog[\s\S]*?-->\s*/g, "")
  .replace(/<script>\s*window\.REDRAFT_PH[\s\S]*?<\/script>\s*/g, "")
  .replace(/<script[^>]*src="\/analytics\.js"[^>]*><\/script>\s*/g, "");

let css = "";
let files = 0;
try {
  await mkdir(`${dist}/fonts`, { recursive: true });
  for (const url of sheets) {
    const res = await fetch(url, { headers: { "User-Agent": UA } });
    if (!res.ok) throw new Error(`${url} -> ${res.status}`);
    let sheet = await res.text();
    for (const src of new Set([...sheet.matchAll(/url\((https:\/\/fonts\.gstatic\.com\/[^)]+)\)/g)].map((m) => m[1]))) {
      const font = await fetch(src, { headers: { "User-Agent": UA } });
      if (!font.ok) throw new Error(`${src} -> ${font.status}`);
      const name = `${createHash("sha1").update(src).digest("hex").slice(0, 12)}.woff2`;
      await writeFile(`${dist}/fonts/${name}`, Buffer.from(await font.arrayBuffer()));
      sheet = sheet.replaceAll(src, `/fonts/${name}`);
      files += 1;
    }
    css += sheet + "\n";
  }
} catch (err) {
  console.warn(`container-ui: fonts not bundled (${err.message}); the page will use system fonts`);
  css = "";
}

if (css) {
  await writeFile(`${dist}/fonts.css`, css);
  html = html.replace("</head>", '  <link rel="stylesheet" href="/fonts.css" />\n  </head>');
}
await writeFile(page, html);

// public/ holds the marketing site's files too; the editor needs none of them but its tab icon.
const keep = new Set(["app.html", "assets", "fonts", "fonts.css", "favicon.js"]);
for (const name of await readdir(dist)) if (!keep.has(name)) await rm(`${dist}/${name}`, { recursive: true });

const left = html.match(/https?:\/\/(?!www\.w3\.org)[^"'\s)<]+/g) || [];
console.log(`container-ui: ${files} font files bundled; hosts left in app.html: ${left.length ? left.join(" ") : "none"}`);
