// End-to-end validation:  QR -> URL -> product page -> 3D model.
//
//   npm run validate                 checks data, files, GLBs, QR codes, built site, menu
//   npm run validate -- --http       also requests every QR URL from a running server
//
// Exit code 1 on any error. Placeholders (data missing from the source menu) are warnings.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import jsQR from 'jsqr';
import { PNG } from 'pngjs';
import jpeg from 'jpeg-js';
import validator from 'gltf-validator';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { MeshoptDecoder } from 'meshoptimizer';
import { ROOT, loadConfig, loadProducts, productPath, productUrl } from './lib/config.mjs';

const cfg = loadConfig();
const products = loadProducts();
const DIST = path.join(ROOT, 'dist');
const errors = [];
const warnings = [];
const err = (m) => errors.push(m);
const warn = (m) => warnings.push(m);
const abs = (p) => path.join(ROOT, p.replace(/^\//, ''));
const exists = (p) => fs.existsSync(abs(p));
const MODEL_BUDGET = 1.5 * 1024 * 1024;

// ---------------------------------------------------------------- 1. product data
const ids = new Map();
const slugs = new Map();
const cats = new Set(cfg.categories.map((c) => c.id));
for (const p of products) {
  const tag = p.slug || p.id || '(unnamed)';
  for (const k of ['id', 'slug', 'category', 'name', 'prices', 'image', 'model', 'qr', 'placeholders']) {
    if (p[k] === undefined) err(`${tag}: missing field "${k}"`);
  }
  if (ids.has(p.id)) err(`duplicate product id "${p.id}" (${ids.get(p.id)} and ${tag})`);
  ids.set(p.id, tag);
  if (slugs.has(p.slug)) err(`duplicate slug "${p.slug}"`);
  slugs.set(p.slug, p.id);
  if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(p.slug || '')) err(`${tag}: slug must be lowercase-kebab-case`);
  if (!cats.has(p.category)) err(`${tag}: unknown category "${p.category}"`);
  if (!p.name?.en?.trim() || !p.name?.ar?.trim()) err(`${tag}: name.en and name.ar are required`);
  for (const pr of p.prices || []) {
    if (!cfg.sizes[pr.size]) err(`${tag}: unknown size "${pr.size}" (add it to config.sizes)`);
    if (!Number.isInteger(pr.amount) || pr.amount <= 0) err(`${tag}: invalid price ${pr.amount}`);
  }
  if (!p.prices?.length && !p.placeholders?.includes('prices')) err(`${tag}: no prices and not marked as placeholder`);
  for (const ph of p.placeholders || []) warn(`${tag}: "${ph}" is a placeholder - not printed on the source menu (see notes)`);
  // convention checks keep paths predictable for adding products later
  const expect = { image: `/assets/products/${p.slug}.jpg`, model: `/models/${p.slug}.glb`, qr: `/qrcodes/${p.slug}.png` };
  for (const [k, v] of Object.entries(expect)) if (p[k] !== v) warn(`${tag}: ${k} is "${p[k]}" (convention: "${v}")`);
}

// ---------------------------------------------------------------- 2. files
// The validator cannot read meshopt-compressed buffers: validate the decoded model.
await MeshoptDecoder.ready;
const gltfIO = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.decoder': MeshoptDecoder });
async function decompressed(buf) {
  const doc = await gltfIO.readBinary(new Uint8Array(buf));
  doc.getRoot().listExtensionsUsed().find((e) => e.extensionName === 'EXT_meshopt_compression')?.dispose();
  return gltfIO.writeBinary(doc);
}
const glbPromises = [];
for (const p of products) {
  for (const k of ['image', 'poster', 'model', 'qr']) {
    if (p[k] && !exists(p[k])) err(`${p.slug}: missing ${k} file ${p[k]}`);
  }
  if (p.qr && !exists(p.qr.replace(/\.png$/, '.svg'))) err(`${p.slug}: missing SVG QR`);
  for (const k of ['menuPage', 'originalFile']) {
    if (p.source?.[k] && !exists(p.source[k])) err(`${p.slug}: missing source ${k} ${p.source[k]}`);
  }
  // the copy in menu/original must be byte-identical to the untouched original in images/
  if (p.source?.menuPage && p.source?.originalFile && exists(p.source.menuPage) && exists(p.source.originalFile)) {
    const h = (f) => crypto.createHash('sha1').update(fs.readFileSync(abs(f))).digest('hex');
    if (h(p.source.menuPage) !== h(p.source.originalFile)) err(`${p.slug}: ${p.source.menuPage} differs from original ${p.source.originalFile}`);
  }
  if (p.model && exists(p.model)) {
    const buf = fs.readFileSync(abs(p.model));
    if (buf.readUInt32LE(0) !== 0x46546c67 || buf.readUInt32LE(4) !== 2) err(`${p.slug}: ${p.model} is not a glTF 2.0 binary`);
    if (buf.length > MODEL_BUDGET) warn(`${p.slug}: model is ${(buf.length / 1048576).toFixed(2)} MB (budget 1.5 MB)`);
    glbPromises.push(decompressed(buf).then((bytes) => validator.validateBytes(bytes, { maxIssues: 50 })).then((r) => {
      if (r.issues.numErrors) err(`${p.slug}: glTF validator: ${r.issues.messages.filter((m) => m.severity === 0).map((m) => m.code).join(', ')}`);
      const tris = r.info?.totalTriangleCount ?? 0;
      return { slug: p.slug, kb: Math.round(buf.length / 1024), tris, warnings: r.issues.numWarnings };
    }).catch((e) => err(`${p.slug}: glTF validator failed: ${e.message || e}`)));
  }
}
const glbStats = (await Promise.all(glbPromises)).filter(Boolean);

// ---------------------------------------------------------------- 3. QR codes
function decodePNG(file) {
  const png = PNG.sync.read(fs.readFileSync(file));
  return jsQR(new Uint8ClampedArray(png.data), png.width, png.height)?.data ?? null;
}
const manifestFile = path.join(ROOT, 'qrcodes', 'manifest.json');
const manifest = fs.existsSync(manifestFile) ? JSON.parse(fs.readFileSync(manifestFile, 'utf8')) : null;
if (!manifest) err('qrcodes/manifest.json missing - run npm run qr');
else if (manifest.baseUrl !== cfg.baseUrl) err(`QR codes were generated for ${manifest.baseUrl} but BASE_URL is ${cfg.baseUrl} - run npm run qr`);
const payloads = new Map();
for (const p of products) {
  if (!p.qr || !exists(p.qr)) continue;
  const data = decodePNG(abs(p.qr));
  const want = productUrl(cfg, p.slug);
  if (!data) err(`${p.slug}: QR image cannot be decoded`);
  else if (data !== want) err(`${p.slug}: QR encodes ${data}, expected ${want}`);
  if (data && payloads.has(data)) err(`duplicate QR payload ${data} (${payloads.get(data)} and ${p.slug})`);
  if (data) payloads.set(data, p.slug);
  if (/^https?:\/\/(localhost|127\.)/.test(want)) {
    if (!warnings.some((w) => w.startsWith('QR codes point to localhost'))) {
      warn('QR codes point to localhost - phones cannot open them. Set BASE_URL to your LAN IP or domain and run npm run all.');
    }
  }
}
for (const f of fs.readdirSync(path.join(ROOT, 'qrcodes')).filter((f) => f.endsWith('.png'))) {
  if (!products.some((p) => path.basename(p.qr) === f)) err(`orphan QR code qrcodes/${f} (no matching product)`);
}

// ---------------------------------------------------------------- 4. built site: routes + local links
const pageFor = (slug) => {
  const rel = productPath(cfg, slug);
  return path.join(DIST, rel.endsWith('/') ? rel + 'index.html' : rel + '.html');
};
if (!fs.existsSync(DIST)) err('dist/ missing - run npm run build');
else {
  const htmlFiles = [];
  const walk = (d) => fs.readdirSync(d, { withFileTypes: true }).forEach((e) => {
    const f = path.join(d, e.name);
    if (e.isDirectory()) walk(f); else if (f.endsWith('.html')) htmlFiles.push(f);
  });
  walk(DIST);
  for (const p of products) {
    const f = pageFor(p.slug);
    if (!fs.existsSync(f)) { err(`${p.slug}: route ${productPath(cfg, p.slug)} not built`); continue; }
    const html = fs.readFileSync(f, 'utf8');
    if (!html.includes(`src="${cfg.basePath}${p.model}"`)) err(`${p.slug}: page does not load its own model ${p.model}`);
    if (!html.includes(`<h1>`) || !html.includes(p.name.en.replace(/&/g, '&amp;'))) err(`${p.slug}: page does not show product name`);
  }
  // every product page in dist must belong to a product (no stale pages)
  const prodDir = path.join(DIST, 'product');
  if (fs.existsSync(prodDir)) {
    for (const d of fs.readdirSync(prodDir)) {
      const slug = d.replace(/\.html$/, '');
      if (!slugs.has(slug)) err(`stale page dist/product/${d} has no product`);
    }
  }
  // all local href/src targets must exist
  const linkRe = /(?:href|src|poster)="([^"#?]+)[^"]*"/g;
  let checked = 0;
  for (const f of htmlFiles) {
    const html = fs.readFileSync(f, 'utf8');
    for (const [, url] of html.matchAll(linkRe)) {
      if (/^(https?:|mailto:|data:)/.test(url)) continue;
      let rel = url.startsWith('/') ? url : path.posix.join(path.posix.dirname('/' + path.relative(DIST, f).replace(/\\/g, '/')), url);
      if (cfg.basePath && rel.startsWith(cfg.basePath)) rel = rel.slice(cfg.basePath.length) || '/';
      const target = path.join(DIST, rel);
      const ok = [target, path.join(target, 'index.html'), target + '.html'].some((t) => fs.existsSync(t) && fs.statSync(t).isFile());
      checked++;
      if (!ok) err(`broken link in ${path.relative(DIST, f)}: ${url}`);
    }
  }
  console.log(`links: ${checked} local references checked in ${htmlFiles.length} pages`);
}

// ---------------------------------------------------------------- 5. final menu: every product has a QR, and it scans
const layout = JSON.parse(fs.readFileSync(path.join(ROOT, 'menu', 'layout.json'), 'utf8'));
const onMenu = new Map();
for (const page of layout.pages) {
  const file = path.join(ROOT, 'menu', 'final', page.file);
  if (!fs.existsSync(path.join(ROOT, 'menu', 'original', page.file))) err(`menu page ${page.file} has no original`);
  if (!fs.existsSync(file)) { err(`menu/final/${page.file} missing - run npm run menu`); continue; }
  const img = page.qrs.length ? jpeg.decode(fs.readFileSync(file), { useTArray: true }) : null;
  for (const q of page.qrs) {
    if (!slugs.has(q.slug)) { err(`${page.file}: QR for unknown product "${q.slug}"`); continue; }
    if (onMenu.has(q.slug)) err(`${q.slug}: placed twice on the menu (${onMenu.get(q.slug)}, ${page.file})`);
    onMenu.set(q.slug, page.file);
    // crop the card region (+ quiet zone) and decode
    const pad = 30, size = q.qr + 120;
    const x0 = Math.max(0, q.x - pad), y0 = Math.max(0, q.y - pad);
    const w = Math.min(img.width - x0, (q.style === 'horizontal' ? q.qr * 3.2 : size) + pad * 2);
    const h = Math.min(img.height - y0, size + pad * 2);
    const crop = new Uint8ClampedArray(Math.round(w) * Math.round(h) * 4);
    for (let y = 0; y < Math.round(h); y++) {
      crop.set(img.data.subarray(((y0 + y) * img.width + x0) * 4, ((y0 + y) * img.width + x0 + Math.round(w)) * 4), y * Math.round(w) * 4);
    }
    const data = jsQR(crop, Math.round(w), Math.round(h))?.data;
    const want = productUrl(cfg, q.slug);
    if (data !== want) err(`${page.file}: printed QR for ${q.slug} decodes to ${data ?? 'nothing'}, expected ${want}`);
  }
}
for (const p of products) if (!onMenu.has(p.slug)) err(`${p.slug}: has no QR on the final menu (menu/layout.json)`);
// ---------------------------------------------------------------- 6. optional: live HTTP check of every QR target
if (process.argv.includes('--http')) {
  for (const p of products) {
    const url = productUrl(cfg, p.slug);
    try {
      const r = await fetch(url);
      if (r.status !== 200) { err(`${url} -> HTTP ${r.status}`); continue; }
      const html = await r.text();
      const m = html.match(/<model-viewer[^>]*\ssrc="([^"]+)"/);
      if (!m) { err(`${url}: no <model-viewer src>`); continue; }
      const mr = await fetch(new URL(m[1], url));
      const type = mr.headers.get('content-type') || '';
      if (mr.status !== 200 || !/gltf-binary|octet-stream/.test(type)) err(`${url}: model ${m[1]} -> HTTP ${mr.status} ${type}`);
    } catch (e) {
      err(`${url}: ${e.cause?.code || e.message} (is the server running? npm run serve)`);
    }
  }
  console.log(`http: ${products.length} QR targets requested`);
}

// ---------------------------------------------------------------- report
console.log(`products: ${products.length} · categories: ${[...new Set(products.map((p) => p.category))].join(', ')}`);
if (glbStats.length) {
  console.log('models:');
  for (const s of glbStats.sort((a, b) => a.slug.localeCompare(b.slug))) {
    console.log(`  ${s.slug.padEnd(26)} ${String(s.kb).padStart(5)} KB  ${String(s.tris).padStart(6)} tris  validator warnings: ${s.warnings}`);
  }
}
for (const w of warnings) console.log('WARN ', w);
for (const e of errors) console.log('ERROR', e);
console.log(errors.length ? `\nFAILED: ${errors.length} error(s)` : `\nOK: QR -> URL -> page -> model verified for ${products.length} products (${warnings.length} warning(s))`);
process.exit(errors.length ? 1 : 0);
