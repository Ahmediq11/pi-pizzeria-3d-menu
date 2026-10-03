// Shared loader for site config + product data. BASE_URL env var overrides config.baseUrl.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

export function loadConfig() {
  const cfg = JSON.parse(fs.readFileSync(path.join(ROOT, 'config', 'site.config.json'), 'utf8'));
  const baseUrl = (process.env.BASE_URL || cfg.baseUrl).replace(/\/+$/, '');
  const url = new URL(baseUrl);
  // A BASE_URL with a path (e.g. https://user.github.io/pi-menu) deploys under that sub-path.
  const basePath = url.pathname.replace(/\/+$/, '');
  return { ...cfg, baseUrl, basePath };
}

export function loadProducts() {
  return JSON.parse(fs.readFileSync(path.join(ROOT, 'products', 'products.json'), 'utf8')).products;
}

export const productPath = (cfg, slug) => cfg.productPath.replace('{slug}', slug);
export const productUrl = (cfg, slug) => cfg.baseUrl + productPath(cfg, slug);
