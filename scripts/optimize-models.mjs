// Optimise the raw Blender exports for mobile web delivery.
//   models/raw/<slug>.glb  ->  models/<slug>.glb
// prune unused data, dedupe, weld, quantise vertex attributes, re-encode the
// textures as WebP (EXT_texture_webp) and compress the geometry with meshopt
// (EXT_meshopt_compression). model-viewer decodes both natively; the meshopt
// decoder is self-hosted (vendor/meshopt_decoder.js, see scripts/build.mjs).
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { dedup, meshopt, prune, quantize, textureCompress, weld } from '@gltf-transform/functions';
import { MeshoptDecoder, MeshoptEncoder } from 'meshoptimizer';
import sharp from 'sharp';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const RAW = path.join(ROOT, 'models', 'raw');
const OUT = path.join(ROOT, 'models');
const only = process.argv.slice(2);

await MeshoptEncoder.ready;
await MeshoptDecoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({
  'meshopt.encoder': MeshoptEncoder,
  'meshopt.decoder': MeshoptDecoder,
});
const files = fs.readdirSync(RAW).filter((f) => f.endsWith('.glb'))
  .filter((f) => !only.length || only.includes(path.basename(f, '.glb')));

let total = 0;
for (const f of files) {
  const doc = await io.read(path.join(RAW, f));
  await doc.transform(
    prune(),
    dedup(),
    weld(),
    quantize({ quantizePosition: 14, quantizeNormal: 10, quantizeTexcoord: 12 }),
    // photo colour: high quality; normal maps: higher still (blocky normals show as lighting artefacts)
    textureCompress({ encoder: sharp, targetFormat: 'webp', slots: /^(?!normalTexture).*$/, quality: 88 }),
    textureCompress({ encoder: sharp, targetFormat: 'webp', slots: /^normalTexture$/, quality: 94 }),
    meshopt({ encoder: MeshoptEncoder, level: 'medium' }),
  );
  const out = path.join(OUT, f);
  await io.write(out, doc);
  const before = fs.statSync(path.join(RAW, f)).size;
  const after = fs.statSync(out).size;
  total += after;
  console.log(`${f.padEnd(30)} ${(before / 1024).toFixed(0).padStart(6)} KB -> ${(after / 1024).toFixed(0).padStart(6)} KB`);
}
console.log(`total ${(total / 1024 / 1024).toFixed(2)} MB for ${files.length} models`);
