import { test, expect } from 'claude-code/testing';

const START = 'mcp__workers__worker_start';
const SEND = 'mcp__workers__worker_send';

// A bridge fixture: `versions` is what /health answers, one entry per bridge process (pid 1, 2, ...).
function setup(on, { versions = ['0.5.0'], ownStatus = 'done' } = {}) {
  const calls = [];
  let pid = 1;
  const down = new Set();
  const worker = { id: 'w1', kind: 'codex', task: 'Task', status: ownStatus, hostSessionId: 'owner', reachable: true,
    origin: 'delegated', tools: [], events: [], history: [], approvals: [], output: '', queued: 0, files: [] };
  on('session.id', async () => ({ value: 'owner' }));
  on('session.cwd', async () => ({ value: '/tmp/project' }));
  on('session.start', async (_, e) => ({ cwd: e.cwd }));
  on('command.register', async (_, e) => ({ value: { command: e.name } }));
  on('tool.register', async (_, e) => ({ value: { tool: 'mcp__workers__' + e.name } }));
  on('tool.call', async () => ({ result: {} }));
  on('clock.now', async () => ({ value: 2000000 }));
  on('clock.every', async () => new Promise(() => {}));
  on('clock.sleep', async () => ({ value: undefined }));
  on('ui.open', async () => ({ value: undefined }));
  on('ui.toast', async () => ({ value: undefined }));
  on('store.get', async () => ({ value: undefined }));
  on('store.set', async () => ({ value: undefined }));
  on('process.run', async () => {
    const p = calls.some(c => c.route === 'shutdown') ? ++pid : pid;
    return { value: { exitCode: 0, stderr: '', stdout: JSON.stringify({ url: 'http://127.0.0.1:' + (9000 + p), token: 't' + p, pid: p }) } };
  });
  on('http.fetch', async (_, e) => {
    const port = Number(new URL(e.url).port);
    if (down.has(port)) throw new Error('connection refused');
    const route = new URL(e.url).pathname.slice(1);
    const raw = e.init?.body ?? e.body;
    const body = raw ? JSON.parse(raw) : undefined;
    calls.push({ route, port, body });
    let value = { ok: true };
    if (route === 'health') value = { version: versions[Math.min(port - 9001, versions.length - 1)] };
    else if (route === 'overview') value = { workers: [worker], agents: [] };
    else if (route === 'start') value = { ...worker, id: 'w2', status: 'starting' };
    else if (route === 'send') value = { delivery: 'queued' };
    else if (route === 'shutdown') down.add(port);
    return { value: { ok: true, status: 200, headers: {}, text: JSON.stringify(value) } };
  });
  return { calls };
}

const parse = (r) => (typeof r.result === 'string' ? JSON.parse(r.result) : r.result);

test('worker_start and worker_send pass files and pasted image numbers to the bridge', async ($, on) => {
  const { calls } = setup(on);
  await $.session.start({ cwd: '/tmp/project' });
  const started = await $.tool.call({ tool: START, kind: 'codex', task: 'Fix the layout', files: ['/tmp/shot.png'], images: [2] });
  expect(started.deny).toBeUndefined();
  expect(calls.find(c => c.route === 'start').body).toMatchObject({ task: 'Fix the layout', files: ['/tmp/shot.png'], images: [2] });
  await $.tool.call({ tool: SEND, id: 'w1', message: 'And this one', files: ['/tmp/spec.pdf'] });
  expect(calls.find(c => c.route === 'send').body).toMatchObject({ id: 'w1', message: 'And this one', files: ['/tmp/spec.pdf'] });
  expect(calls.some(c => c.route === 'shutdown')).toBe(false);
});

test('without attachments the bridge version is not checked', async ($, on) => {
  const { calls } = setup(on, { versions: ['0.4.1'] });
  await $.session.start({ cwd: '/tmp/project' });
  const started = await $.tool.call({ tool: START, kind: 'qwen', task: 'Plain task' });
  expect(started.deny).toBeUndefined();
  expect(calls.some(c => c.route === 'health')).toBe(false);
});

test('an older idle bridge is restarted before the first attachment', async ($, on) => {
  const { calls } = setup(on, { versions: ['0.4.1', '0.5.0'] });
  await $.session.start({ cwd: '/tmp/project' });
  const started = await $.tool.call({ tool: START, kind: 'codex', task: 'Look', files: ['/tmp/shot.png'] });
  expect(started.deny).toBeUndefined();
  const shutdown = calls.findIndex(c => c.route === 'shutdown');
  const start = calls.find(c => c.route === 'start');
  expect(shutdown).toBeGreaterThan(-1);
  expect(start.port).toBe(9002); // the new bridge took the request
  expect(parse(started).id).toBe('w2');
});

test('an older bridge with a running worker is left alone and the call says why', async ($, on) => {
  const { calls } = setup(on, { versions: ['0.4.1'], ownStatus: 'running' });
  await $.session.start({ cwd: '/tmp/project' });
  const started = await $.tool.call({ tool: START, kind: 'codex', task: 'Look', files: ['/tmp/shot.png'] });
  expect(started.deny).toContain('older than attachments support');
  expect(calls.some(c => c.route === 'shutdown' || c.route === 'start')).toBe(false);
});
