// One QR code per product -> qrcodes/<slug>.png (+ .svg for print) and qrcodes/manifest.json.
// The encoded URL is BASE_URL + productPath, so changing BASE_URL only needs `npm run qr`.
import QRCode from 'qrcode';
import fs from 'node:fs';
import path from 'node:path';
import { ROOT, loadConfig, loadProducts, productUrl } from './lib/config.mjs';

const cfg = loadConfig();
const products = loadProducts();
const dir = path.join(ROOT, 'qrcodes');
fs.mkdirSync(dir, { recursive: true });

const opts = {
  errorCorrectionLevel: cfg.qr.errorCorrectionLevel,
  margin: cfg.qr.margin,
  color: { dark: cfg.qr.dark, light: cfg.qr.light },
};
const manifest = { baseUrl: cfg.baseUrl, generatedAt: new Date().toISOString(), codes: {} };
for (const p of products) {
  const url = productUrl(cfg, p.slug);
  const file = path.basename(p.qr);
  await QRCode.toFile(path.join(dir, file), url, { ...opts, type: 'png', width: cfg.qr.width });
  fs.writeFileSync(path.join(dir, file.replace(/\.png$/, '.svg')), await QRCode.toString(url, { ...opts, type: 'svg' }));
  manifest.codes[p.slug] = { id: p.id, url, png: `qrcodes/${file}` };
}
// remove codes of products that no longer exist, so no stale QR can be printed
const keep = new Set(products.flatMap((p) => [path.basename(p.qr), path.basename(p.qr).replace(/\.png$/, '.svg')]));
for (const f of fs.readdirSync(dir)) {
  if (/\.(png|svg)$/.test(f) && !keep.has(f)) fs.rmSync(path.join(dir, f));
}
fs.writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify(manifest, null, 2));
console.log(`QR codes: ${products.length} -> ${cfg.baseUrl}${cfg.productPath}`);
