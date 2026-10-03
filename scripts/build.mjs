// Build the static site into dist/ from products/products.json.
// One product template -> dist/product/<slug>/index.html for every product.
import fs from 'node:fs';
import path from 'node:path';
import { ROOT, loadConfig, loadProducts, productPath, productUrl } from './lib/config.mjs';
import { menuPage, notFoundPage, productPage, qrSheetPage } from '../site/templates.mjs';

const cfg = loadConfig();
const products = loadProducts();
const layout = JSON.parse(fs.readFileSync(path.join(ROOT, 'menu', 'layout.json'), 'utf8'));
const DIST = path.join(ROOT, 'dist');

fs.rmSync(DIST, { recursive: true, force: true });
const write = (rel, content) => {
  const f = path.join(DIST, rel);
  fs.mkdirSync(path.dirname(f), { recursive: true });
  fs.writeFileSync(f, content);
};
const copy = (from, to, filter = () => true) => {
  const src = path.join(ROOT, from);
  if (!fs.existsSync(src)) throw new Error(`missing ${from}`);
  fs.cpSync(src, path.join(DIST, to), { recursive: true, filter: (s) => fs.statSync(s).isDirectory() || filter(s) });
};

// ---- static assets
copy('site/assets', 'assets');
copy('assets/products', 'assets/products');
copy('assets/posters', 'assets/posters');
copy('assets/brand', 'assets/brand');
for (const f of fs.readdirSync(path.join(ROOT, 'models')).filter((f) => f.endsWith('.glb'))) {
  copy(`models/${f}`, `models/${f}`); // optimised models only (not models/raw or models/source)
}
copy('qrcodes', 'qrcodes', (f) => /\.(png|svg)$/.test(f));
copy('menu/final', 'menu');
copy('node_modules/@google/model-viewer/dist/model-viewer.min.js', 'vendor/model-viewer.min.js');
// decoder for EXT_meshopt_compression (the models' geometry), self-hosted instead of a CDN
copy('node_modules/meshoptimizer/meshopt_decoder.cjs', 'vendor/meshopt_decoder.js');

// ---- pages
const order = cfg.categories.map((c) => c.id);
const sorted = [...products].sort((a, b) =>
  order.indexOf(a.category) - order.indexOf(b.category) || (a.menuNumber ?? 0) - (b.menuNumber ?? 0));
const catOf = (id) => cfg.categories.find((c) => c.id === id);
const pageFile = (p) => {
  const rel = productPath(cfg, p.slug);
  return rel.endsWith('/') ? [rel + 'index.html'] : [rel + '.html', rel + '/index.html'];
};

sorted.forEach((p, i) => {
  const ref = (q) => q && { path: productPath(cfg, q.slug), name: q.name.en };
  const html = productPage({
    p, cfg, url: productUrl(cfg, p.slug), category: catOf(p.category),
    index: { prev: ref(sorted[i - 1]), next: ref(sorted[i + 1]), pos: i + 1 }, total: sorted.length,
  });
  for (const f of pageFile(p)) write(f, html);
});

const groups = cfg.categories
  .map((category) => ({ category, items: sorted.filter((p) => p.category === category.id).map((p) => ({ p, path: productPath(cfg, p.slug) })) }))
  .filter((g) => g.items.length);
write('index.html', menuPage({ cfg, groups, url: cfg.baseUrl + '/', menuPages: { pdf: layout.output.pdf } }));
write('qr/index.html', qrSheetPage({ cfg, url: cfg.baseUrl + '/qr/', items: sorted.map((p) => ({ p, link: productUrl(cfg, p.slug) })) }));
write('404.html', notFoundPage({ cfg }));

// public product data (internal notes / source paths stripped)
write('products.json', JSON.stringify({
  baseUrl: cfg.baseUrl,
  products: sorted.map(({ notes, source, placeholders, ...p }) => ({ ...p, url: productUrl(cfg, p.slug) })),
}, null, 2));

// ---- hosting helpers (Netlify / Cloudflare Pages read _headers; GitHub Pages needs .nojekyll)
write('_headers', `/models/*
  Content-Type: model/gltf-binary
  Cache-Control: public, max-age=604800
/assets/*
  Cache-Control: public, max-age=604800
/vendor/*
  Cache-Control: public, max-age=2592000, immutable
`);
write('.nojekyll', '');

console.log(`built ${sorted.length} product pages -> dist/ (base ${cfg.baseUrl})`);
