// Exercise Vite's real watcher without opening a window or writing files.
import { fileURLToPath } from 'node:url';
import { createServer } from '../desktop/node_modules/vite/dist/node/index.js';
const root = fileURLToPath(new URL('../desktop', import.meta.url));
// Exercise normal config discovery too: a stale generated vite.config.js must
// not silently shadow the maintained TypeScript configuration.
const server = await createServer({ root, server: { middlewareMode: true } });
try {
  if (!server.config.configFile?.replaceAll('\\', '/').endsWith('/vite.config.ts'))
    throw new Error(`Unexpected Vite config: ${server.config.configFile}`);
  await new Promise(resolve => setTimeout(resolve, 1500));
  const watched = Object.entries(server.watcher.getWatched());
  if (watched.length < 3) throw new Error('Watcher did not initialize');
  const rust = watched.flatMap(([directory, children]) => [directory, ...children.map(child => `${directory}/${child}`)])
    .filter(path => /(^|[\\/])src-tauri([\\/]|$)/i.test(path));
  if (rust.length) throw new Error(`Rust paths still watched: ${rust.join(', ')}`);
  if (!watched.some(([directory, children]) => /[\\/]src$/.test(directory) && children.includes('WeaponCamera.tsx')))
    throw new Error('Frontend source is no longer watched');
  console.log(`PASS: ${watched.length} watched directories; frontend active, entire src-tauri tree excluded.`);
} finally { await server.close(); }
