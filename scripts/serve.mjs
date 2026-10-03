// Zero-dependency static server for dist/ (also reachable from phones on the same Wi-Fi).
// PORT=4173 by default. Serves /product/<slug> and /product/<slug>/ alike.
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';
import { ROOT, loadConfig } from './lib/config.mjs';

const DIST = path.join(ROOT, 'dist');
const PORT = Number(process.env.PORT || 4173);
const { basePath } = loadConfig();
const TYPES = {
  '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.glb': 'model/gltf-binary', '.png': 'image/png', '.jpg': 'image/jpeg',
  '.webp': 'image/webp', '.svg': 'image/svg+xml', '.pdf': 'application/pdf', '.ico': 'image/x-icon',
};
const COMPRESS = new Set(['.html', '.css', '.js', '.json', '.glb', '.svg']);

function resolve(urlPath) {
  let p = decodeURIComponent(urlPath.split('?')[0]);
  if (basePath && p.startsWith(basePath)) p = p.slice(basePath.length) || '/';
  const base = path.join(DIST, p); // join() resolves "..", so check we are still inside dist/
  if (base !== DIST && !base.startsWith(DIST + path.sep)) return null;
  for (const c of [base, path.join(base, 'index.html'), base + '.html']) {
    if (fs.existsSync(c) && fs.statSync(c).isFile()) return c;
  }
  return null;
}

http.createServer((req, res) => {
  const file = resolve(req.url);
  const status = file ? 200 : 404;
  const f = file || path.join(DIST, '404.html');
  const ext = path.extname(f);
  const headers = { 'Content-Type': TYPES[ext] || 'application/octet-stream', 'Cache-Control': 'no-cache' };
  let body = fs.readFileSync(f);
  if (COMPRESS.has(ext) && /\bgzip\b/.test(req.headers['accept-encoding'] || '')) {
    body = zlib.gzipSync(body);
    headers['Content-Encoding'] = 'gzip';
  }
  res.writeHead(status, headers);
  res.end(req.method === 'HEAD' ? undefined : body);
  if (status !== 200) console.log(404, req.url);
}).listen(PORT, '0.0.0.0', () => {
  console.log(`serving dist/ on http://localhost:${PORT}${basePath}/`);
  for (const nets of Object.values(os.networkInterfaces())) {
    for (const n of nets || []) {
      if (n.family === 'IPv4' && !n.internal) console.log(`  on your Wi-Fi: http://${n.address}:${PORT}${basePath}/`);
    }
  }
  console.log('To test QR codes from a phone: BASE_URL=http://<your-LAN-IP>:' + PORT + ' npm run all, then npm run serve');
});
