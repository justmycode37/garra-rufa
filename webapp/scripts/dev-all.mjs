import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const app = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const backend = process.env.GARRA_BACKEND_DIR || [path.resolve(app, '..'), path.join(app, '.backend')].find(root => existsSync(path.join(root, 'src/garra/ui/server.py')));
if (!backend) {
  console.error('Python backend not found. Clone the repository and run from webapp/, or set GARRA_BACKEND_DIR to its root.');
  process.exit(1);
}
const python = process.env.GARRA_PYTHON || [path.join(backend, '.venv/bin/python'), path.join(backend, '.venv/Scripts/python.exe')].find(existsSync) || 'python3';
const children = [];
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) child.kill('SIGTERM');
  process.exitCode = code;
}
function run(command, args, cwd, env) {
  const child = spawn(command, args, { cwd, env: { ...process.env, ...env }, stdio: 'inherit' });
  children.push(child);
  child.on('error', error => { console.error(error.message); stop(1); });
  child.on('exit', code => { if (!stopping) stop(code || 0); });
  return child;
}
const port = process.env.GARRA_RESEARCH_PORT || '8787';
const backendUrl = `http://127.0.0.1:${port}`;
const backendOnly = process.argv.includes('--backend-only');
let running = false;
try {
  const response = await fetch(`${backendUrl}/health`, { signal: AbortSignal.timeout(1000) });
  const health = await response.json();
  if (response.ok && health.service === 'garra-ui-bridge' && health.research && health.regions && health.discovery?.coverage?.clusters > 0) running = true;
  else throw new Error('Unexpected service');
} catch { /* Start the configured backend below; occupied ports fail explicitly. */ }
if (!running) {
  const bridge = process.env.GARRA_BRIDGE || path.join(backend, 'data/derived/discovery/bridge.json');
  const ontology = process.env.GARRA_HPO_ONTOLOGY || path.join(backend, 'data/ontology/hp.obo');
  const communities = process.env.GARRA_COMMUNITIES || path.join(backend, 'examples/communities.demo.json');
  // Missing datasets are restored or skipped by the backend (src/garra/datasets.py).
  for (const file of [bridge, ontology]) if (!existsSync(file)) console.warn(`Discovery dataset missing: ${file}. The backend will restore it or run without it.`);
  run(python, ['-m', 'garra.ui', '--port', port, '--bridge', bridge, '--ontology', ontology, '--atlas', path.join(backend, 'data/atlas.sqlite'), ...(existsSync(communities) ? ['--communities', communities] : [])], backend, { PYTHONPATH: path.join(backend, 'src') });
  for (let attempt = 0; attempt < 50 && !stopping; attempt++) {
    try {
      const response = await fetch(`${backendUrl}/health`, { signal: AbortSignal.timeout(500) });
      const health = await response.json();
      if (health.research && health.service === 'garra-ui-bridge') { running = true; break; }
    } catch { /* Wait for the Python process to bind. */ }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
}
if (!running) { console.error('Research backend did not start. Install dependencies with python3 -m pip install -e ".[research]" in the repository root.'); stop(1); }
else {
  console.log(`Research backend: ${backendUrl}`);
  if (!backendOnly) run(process.execPath, [path.join(app, 'node_modules/next/dist/bin/next'), 'dev', '--hostname', '127.0.0.1'], app, { GARRA_RESEARCH_URL: backendUrl, WATCHPACK_POLLING: process.env.WATCHPACK_POLLING || '1000' });
}
process.on('SIGINT', () => stop());
process.on('SIGTERM', () => stop());
