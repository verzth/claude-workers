import { test, expect } from 'claude-code/testing';

const START = 'mcp__workers__worker_start';
function setup(on) {
  let disconnected = false;
  const opened = [];
  const workers = ['running', 'waiting', 'error', 'done', 'idle', 'interrupted', 'offline'].map(status => ({
    id: status, kind: 'codex', task: 'Task ' + status, status, hostSessionId: 'owner',
    reachable: status !== 'offline', origin: 'direct', tools: [], events: [], history: [], approvals: [], output: '', queued: 0,
  }));
  workers.push({ ...workers[0], id: 'peer', hostSessionId: 'other' });
  workers.push({ ...workers[0], id: 'archived' });
  on('session.id', async () => ({ value: 'owner' }));
  on('session.cwd', async () => ({ value: '/tmp/project' }));
  on('session.start', async (_, e) => ({ cwd: e.cwd }));
  on('command.register', async (_, e) => ({ value: { command: e.name } }));
  on('tool.register', async (_, e) => ({ value: { tool: 'mcp__workers__' + e.name } }));
  on('clock.now', async () => ({ value: 2000000 }));
  on('clock.every', async () => new Promise(() => {}));
  on('ui.open', async (_, e) => { opened.push(e); return { value: undefined }; });
  on('ui.toast', async () => ({ value: undefined }));
  on('store.get', async (_, e) => ({ value: e.key?.endsWith(':archived') ? { archived: true } : undefined }));
  on('store.set', async () => ({ value: undefined }));
  on('process.run', async () => ({ value: { exitCode: 0, stderr: '', stdout: '{"url":"http://127.0.0.1:9999","token":"fixture","pid":1}' } }));
  on('http.fetch', async (_, e) => ({ value: { ok: !disconnected, status: disconnected ? 503 : 200, headers: {},
    text: disconnected ? '{"error":"unavailable"}' : e.url.endsWith('/overview') ? JSON.stringify({ workers, agents: [] }) : '{"ok":true}' } }));
  for (const component of ['ToolUse', 'ToolResult', 'ToolGroup', 'CommandOutput']) {
    on('ui.render', { component }, async ($, e) => $.ui.resolve(e).Text({ children: ['Native ' + component] }));
  }
  return { workers, opened, disconnect: () => { disconnected = true; } };
}
async function mount($, component = 'CommandOutput', props = { command: 'workers', text: 'Opened', isErrored: false }, surface = 'desktop') {
  await $.session.start({ cwd: '/tmp/project' });
  await $.command.run({ command: 'workers' });
  return $.ui.mount({ plugin: 'workers', surface, component, requestId: 'fixture', props });
}

test('progress separates statuses, scopes to current session, excludes archived and opens Workers', async ($, on) => {
  const f = setup(on);
  const ui = await mount($);
  expect((await ui.find({ key: 'open-worker-progress' }))?.text).toBe('Workers · 1 running · 1 need input · 1 failed · 1 done · 1 ready · 1 stopped · 1 offline');
  await ui.press({ key: 'open-worker-progress' });
  expect(f.opened.at(-1).focus).toBe(true);
  await ui.unmount();
});

test('mounted progress refreshes and disconnected bridge marks counts last known', async ($, on) => {
  const f = setup(on);
  const ui = await mount($);
  f.workers[0].status = 'done';
  await $.command.run({ command: 'workers' });
  expect((await ui.find({ key: 'open-worker-progress' }))?.text).toContain('2 done');
  expect((await ui.find({ key: 'open-worker-progress' }))?.text).not.toContain('running');
  f.disconnect();
  await $.command.run({ command: 'workers' });
  expect((await ui.find({ key: 'open-worker-progress' }))?.text).toContain('Last known:');
  expect(await ui.find({ text: 'Bridge disconnected · Click to reconnect' })).toBeDefined();
  await ui.unmount();
});

test('restored worker_start output opens its detail without displaying raw results twice', async ($, on) => {
  setup(on);
  const output = [{ type: 'text', text: JSON.stringify({ id: 'done', kind: 'codex' }) }];
  const ui = await mount($, 'ToolUse', { tool_use_id: 'old-call', tool: START, output, input: {}, isRunning: false, isErrored: false, isInterrupted: false });
  expect(await ui.find({ text: 'Codex · Done · Task done' })).toBeDefined();
  await ui.press({ key: 'open-worker-progress' });
  const pane = await $.ui.mount({ plugin: 'workers', surface: 'desktop', component: 'Pane', requestId: 'workers', props: { bodyColumns: 44 } });
  expect(await pane.find({ key: 'back-overview' })).toBeDefined();
  await pane.unmount();
  await ui.unmount();
  const result = await $.ui.mount({ plugin: 'workers', surface: 'desktop', component: 'ToolResult', requestId: 'old-call', props: { tool_use_id: 'old-call', tool: START, output, isErrored: false } });
  expect(await result.find({ text: 'Native ToolResult' })).toBeUndefined();
  expect(await result.find({ key: 'worker-progress-card' })).toBeUndefined();
  await result.unmount();
});

test('failed, interrupted and unrelated tool rows retain native rendering', async ($, on) => {
  setup(on);
  await $.session.start({ cwd: '/tmp/project' });
  for (const props of [{ tool: START, isErrored: true }, { tool: START, isInterrupted: true }, { tool: 'Bash', isRunning: true }]) {
    const ui = await $.ui.mount({ plugin: 'workers', surface: 'desktop', component: 'ToolUse', props });
    expect(await ui.find({ text: 'Native ToolUse' })).toBeDefined();
    expect(await ui.find({ key: 'worker-progress-card' })).toBeUndefined();
    await ui.unmount();
  }
});

test('collapsed worker groups get one card while mixed groups retain native background controls', async ($, on) => {
  setup(on);
  const call = { tool: START, isRunning: true, isErrored: false, isInterrupted: false, input: { kind: 'codex' } };
  const ui = await mount($, 'ToolGroup', { calls: [call, call], isExpanded: false, isActive: true });
  expect((await ui.findAll({ key: 'worker-progress-card' })).length).toBe(1);
  expect(await ui.find({ text: 'Native ToolGroup' })).toBeUndefined();
  await ui.unmount();
  for (const isExpanded of [false, true]) {
    const mixed = await $.ui.mount({ plugin: 'workers', surface: 'desktop', component: 'ToolGroup', props: { calls: [call, { tool: 'Bash', isRunning: true }], isExpanded, isActive: true } });
    expect(await mixed.find({ text: 'Native ToolGroup' })).toBeDefined();
    expect(!!await mixed.find({ key: 'worker-progress-card' })).toBe(!isExpanded);
    await mixed.unmount();
  }
});

for (const surface of ['terminal', 'vscode']) {
  test('progress card uses supported native elements on ' + surface, async ($, on) => {
    setup(on);
    const ui = await mount($, 'CommandOutput', { command: 'workers:workers', text: '', isErrored: false }, surface);
    expect(await ui.find({ key: 'open-worker-progress' })).toBeDefined();
    await ui.unmount();
  });
}
