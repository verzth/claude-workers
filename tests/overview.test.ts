import { test, expect } from 'claude-code/testing';

function setup(on, legacy = false) {
  const calls = [];
  const worker = (id, status, host = 'current', origin = 'delegated') => ({
    id, kind: id === 'manual' ? 'opencode' : 'codex', task: 'Task ' + id, status,
    hostSessionId: host, parentTitle: host === 'current' ? 'UI redesign' : 'Bridge review', origin,
    cwd: '/tmp/project', updated: 1990, output: '', history: [], events: [], queued: 0,
    tools: [{ title: 'Read file for ' + id, status: 'completed' }],
    approvals: status === 'waiting' ? [{ id: 'approval', title: 'Run tests?', details: 'python3 -m unittest' }] : [],
    canMessage: status !== 'offline', reachable: status !== 'offline',
  });
  const workers = [worker('done', 'done'), worker('busy', 'running'), worker('failed', 'error'),
    worker('input', 'waiting'), worker('manual', 'offline', 'other', 'direct'), worker('peer', 'running', 'other')];
  on('store.get', async () => ({ value: undefined }));
  on('store.set', async () => ({ value: undefined }));
  on('clock.now', async () => ({ value: 2000000 }));
  on('session.id', async () => ({ value: 'current' }));
  on('session.cwd', async () => ({ value: '/tmp/project' }));
  on('session.start', async (_, e) => ({ cwd: e.cwd }));
  on('command.register', async (_, e) => ({ value: { command: e.name } }));
  on('tool.register', async (_, e) => ({ value: { tool: 'mcp__workers__' + e.name } }));
  on('ui.open', async () => ({ value: undefined }));
  on('ui.toast', async () => ({ value: undefined }));
  on('clock.every', async () => new Promise(() => {}));
  on('process.run', async () => ({ value: { exitCode: 0, stdout: '{"url":"http://127.0.0.1:9999","token":"fixture","pid":1}', stderr: '' } }));
  on('http.fetch', async (_, e) => {
    const route = e.url.split('/').pop();
    if (legacy && route === 'context') return { value: { ok: false, status: 404, headers: {}, text: '{"error":"Unknown endpoint"}' } };
    const body = JSON.parse(e.init?.body || '{}');
    if (route === 'overview' || route === 'state') return { value: { ok: true, status: 200, headers: {}, text: JSON.stringify({ agents: [], workers }) } };
    if (route !== 'context') calls.push({ route, ...body });
    return { value: { ok: true, status: 200, headers: {}, text: '{"ok":true,"delivery":"queued"}' } };
  });
  return { calls, workers };
}

async function mount($, columns = 120) {
  await $.session.start({ cwd: '/tmp/project' });
  await $.command.run({ command: 'workers' });
  return $.ui.mount({ plugin: 'workers', surface: 'desktop', component: 'Pane', requestId: 'workers', props: { bodyColumns: columns } });
}

test('overview groups attention first and filters current, active and all sessions', async ($, on) => {
  setup(on);
  const ui = await mount($);
  expect((await ui.find({ key: 'toggle-Idle' }))?.text).toContain('Idle · 2');
  expect((await ui.find({ key: 'toggle-Working' }))?.text).toContain('Working · 1');
  expect((await ui.find({ key: 'toggle-Finish' }))?.text).toContain('Finish · 1');
  expect(await ui.find({ key: 'pick-peer' })).toBeUndefined();
  const sections = (await ui.findAll({ type: 'Box' })).filter(x => x.props.key?.startsWith('section-')).map(x => x.props.key);
  expect(sections).toEqual(['section-Idle', 'section-Working', 'section-Finish']);
  await ui.press({ key: 'filter-all' });
  expect(await ui.find({ key: 'pick-peer' })).toBeDefined();
  expect((await ui.find({ text: 'From Claude: Bridge review' }))?.text).toContain('From Claude: Bridge review');
  expect((await ui.find({ type: 'Text', text: 'Direct session' }))?.text).toBe('Direct session');
  await ui.press({ key: 'filter-active' });
  expect(await ui.find({ key: 'pick-input' })).toBeDefined();
  expect(await ui.find({ key: 'pick-busy' })).toBeDefined();
  expect(await ui.find({ key: 'pick-peer' })).toBeDefined();
  expect(await ui.find({ key: 'pick-failed' })).toBeUndefined();
  expect(await ui.find({ key: 'pick-done' })).toBeUndefined();
  expect(await ui.find({ key: 'pick-manual' })).toBeUndefined();
  await ui.unmount();
});

test('compact overview collapses Finish and opens details as a secondary view', async ($, on) => {
  setup(on);
  const ui = await mount($, 44);
  expect(await ui.find({ key: 'session-sidebar' })).toBeDefined();
  expect(await ui.find({ key: 'session-detail' })).toBeUndefined();
  expect(await ui.find({ key: 'pick-done' })).toBeUndefined();
  await ui.press({ key: 'toggle-Finish' });
  expect(await ui.find({ key: 'pick-done' })).toBeDefined();
  await ui.press({ key: 'pick-input' });
  expect(await ui.find({ key: 'session-sidebar' })).toBeUndefined();
  expect(await ui.find({ key: 'session-detail' })).toBeDefined();
  expect(await ui.find({ key: 'allow-approval' })).toBeDefined();
  await ui.press({ key: 'back-overview' });
  expect(await ui.find({ key: 'session-sidebar' })).toBeDefined();
  await ui.unmount();
});

test('cross-parent chat and interrupt carry selected parent identity', async ($, on) => {
  const { calls } = setup(on);
  const ui = await mount($);
  await ui.press({ key: 'filter-all' });
  await ui.press({ key: 'pick-peer' });
  expect(await ui.find({ key: 'session-sidebar' })).toBeDefined();
  await ui.input({ key: 'message-peer', text: 'Check lifecycle', kind: 'change' });
  await ui.input({ key: 'message-' + 'peer', text: 'Check lifecycle' });
  await ui.press({ key: 'interrupt-worker' });
  expect(calls).toEqual([
    { route: 'control', hostSessionId: 'other', id: 'peer', action: 'send', message: 'Check lifecycle' },
    { route: 'control', hostSessionId: 'other', id: 'peer', action: 'interrupt' },
  ]);
  await ui.unmount();
});

test('approval stays on selected worker and offline detail has no active controls', async ($, on) => {
  const { calls } = setup(on);
  const ui = await mount($);
  await ui.press({ key: 'pick-input' });
  await ui.press({ key: 'deny-approval' });
  expect(calls[0]).toEqual({ route: 'control', hostSessionId: 'current', id: 'input', action: 'decide', approvalId: 'approval', choice: 'deny' });
  await ui.press({ key: 'filter-all' });
  await ui.press({ key: 'pick-manual' });
  expect(await ui.find({ key: 'send-message' })).toBeUndefined();
  expect(await ui.find({ key: 'interrupt-worker' })).toBeUndefined();
  expect((await ui.find({ text: 'Parent session is offline' }))?.text).toContain('Parent session is offline');
  await ui.unmount();
});

test('tool-facing status never returns another Claude session’s workers', async ($, on) => {
  setup(on);
  on('tool.call', async () => ({ result: {} }));
  await $.session.start({ cwd: '/tmp/project' });
  const result = await $.tool.call({ tool: 'mcp__workers__worker_status' });
  expect((typeof result.result === 'string' ? JSON.parse(result.result) : result.result).workers.map(w => w.id)).toEqual(['done', 'busy', 'failed', 'input']);
});

test('legacy daemon falls back without restarting existing workers', async ($, on) => {
  const { calls } = setup(on, true);
  const ui = await mount($);
  expect((await ui.find({ type: 'Text', text: 'Cross-session view' }))?.text).toContain('new Claude sessions');
  expect(calls).toEqual([]);
  await ui.unmount();
});
