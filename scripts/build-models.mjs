// Rebuild the 3D models headlessly with Blender, then optimise them for the web.
//   npm run models                      all products
//   npm run models -- pizza-italian     only some
// Blender is taken from $BLENDER, else `blender` on PATH.
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { ROOT } from './lib/config.mjs';

const slugs = process.argv.slice(2);
const blender = process.env.BLENDER || 'blender';
const args = ['-b', '--factory-startup', '-P', path.join(ROOT, 'blender', 'build_models.py'), ...(slugs.length ? ['--', ...slugs] : [])];
const run = (cmd, a) => {
  const r = spawnSync(cmd, a, { stdio: 'inherit', cwd: ROOT });
  if (r.error) { console.error(`cannot run ${cmd}: ${r.error.message}\nSet BLENDER=/path/to/blender`); process.exit(1); }
  if (r.status) process.exit(r.status);
};
run(blender, args);
run(process.execPath, [path.join(ROOT, 'scripts', 'optimize-models.mjs'), ...slugs]);
// keep models/source/menu-models.blend (one scene per product) in step with the GLBs
run(blender, ['-b', '--factory-startup', '-P', path.join(ROOT, 'blender', 'save_blend.py')]);
