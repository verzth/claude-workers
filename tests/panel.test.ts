import { test, expect } from 'claude-code/testing';

// Exercise the real render tree and callbacks under Claude's own Mods runtime.
for (const surface of ['terminal', 'desktop', 'vscode'] as const) {
  test(`empty pane renders native controls on ${surface}`, async ($, on) => {
  on('store.get', async () => ({ value: undefined }));
  on('store.set', async () => ({ value: undefined }));
  on('clock.now', async () => ({ value: 2000000 }));
    const ui = await $.ui.mount({ plugin: 'workers', surface, component: 'Pane', requestId: 'workers', props: {} });
    expect((await ui.find({ text: 'No sessions yet' }))?.text).toContain('No sessions yet');
    expect(await ui.find({ key: 'toggle-Idle' })).toBeUndefined();
    expect(await ui.find({ key: 'toggle-Working' })).toBeUndefined();
    expect(await ui.find({ key: 'toggle-Finish' })).toBeUndefined();
    await ui.press({ key: 'new-session' });
    const harness = await ui.find({ key: 'launch-harness' });
    expect(harness?.props.options.map(o => o.value)).toEqual(['codex', 'qwen', 'opencode']);
    expect(await ui.find({ key: 'launch-model-codex' })).toBeDefined();
    await ui.select({ key: 'launch-harness', value: 'qwen' });
    expect((await ui.find({ key: 'task' }))?.props.placeholder).toContain('Qwen Code');
    expect(await ui.find({ key: 'back-overview' })).toBeDefined();
    await ui.unmount();
  });
}

test('worker selection, message and interrupt hit the selected worker', async ($, on) => {
  on('store.get', async () => ({ value: undefined }));
  on('store.set', async () => ({ value: undefined }));
  on('clock.now', async () => ({ value: 2000000 }));
  const calls = [];
  on('session.id', async () => ({ value: 'test-session' }));
  on('session.cwd', async () => ({ value: '/tmp/test-workspace' }));
  on('session.start', async (_, e) => ({ cwd: e.cwd }));
  on('command.register', async (_, e) => ({ value: { command: e.name } }));
  on('tool.register', async (_, e) => ({ value: { tool: 'mcp__workers__' + e.name } }));
  on('ui.open', async () => ({ value: undefined }));
  on('ui.toast', async () => ({ value: undefined }));
  on('clock.every', async () => new Promise(() => {}));
  on('process.run', async () => ({ value: { exitCode: 0, stdout: '{"url":"http://127.0.0.1:9999","token":"fixture","pid":1}', stderr: '' } }));
  on('http.fetch', async (_, e) => {
    const worker = (id, kind) => ({ id, kind, cwd: '/tmp/test-workspace', task: 'Review fixtures',
      status: 'running', queued: 0, error: '', output: 'Live reply', canMessage: true,
      tools: [{ id: 'read', title: 'Read file', status: 'completed' }], events: [], approvals: [] });
    if (e.url.endsWith('/context')) return { value: { status: 200, ok: true, headers: {}, text: '{"ok":true}' } };
    if (e.url.endsWith('/overview')) return { value: { status: 200, ok: true, headers: {}, text: JSON.stringify({ agents: [], workers: [worker('one', 'codex'), worker('two', 'qwen')] }) } };
    const data = JSON.parse(e.init?.body || '{}');
    calls.push(data.action || e.url.split('/').pop());
    if (data.action === 'send') {
      expect(data.id).toBe('two');
      expect(data.message).toBe('Check edge cases');
      return { value: { status: 200, ok: true, headers: {}, text: '{"delivery":"queued"}' } };
    }
    if (data.action === 'interrupt') {
      expect(data.id).toBe('two');
      return { value: { status: 200, ok: true, headers: {}, text: '{"ok":true}' } };
    }
    throw new Error('Unexpected endpoint');
  });
  await $.session.start({ cwd: '/tmp/test-workspace' });
  await $.command.run({ command: 'workers' });
  const ui = await $.ui.mount({ plugin: 'workers', surface: 'terminal', component: 'Pane', requestId: 'workers', props: {} });
  await ui.press({ key: 'pick-two' });
  expect((await ui.find({ text: 'Qwen Code' }))?.text).toContain('Qwen Code');
  expect((await ui.find({ text: 'Read file' }))?.text).toContain('Read file');
  await ui.input({ key: 'message-two', text: 'Check edge cases', kind: 'change' });
  await ui.press({ key: 'back-overview' });
  await ui.press({ key: 'pick-one' });
  await ui.input({ key: 'message-one', text: 'A different draft', kind: 'change' });
  await ui.press({ key: 'back-overview' });
  await ui.press({ key: 'pick-two' });
  expect((await ui.find({ key: 'message-two' }))?.props.value).toBe('Check edge cases');
  await ui.input({ key: 'message-' + 'two', text: 'Check edge cases' });
  await ui.press({ key: 'interrupt-worker' });
  expect(calls).toEqual(['send', 'interrupt']);
  expect(await ui.find({ text: 'Unexpected endpoint' })).toBeUndefined();
  await ui.unmount();
});


test('launcher sends the task, workspace and native model to the bridge', async ($, on) => {
  on('store.get', async () => ({ value: undefined }));
  on('store.set', async () => ({ value: undefined }));
  on('clock.now', async () => ({ value: 2000000 }));
  let payload;
  on('session.id', async () => ({ value: 'launch-test' }));
  on('session.cwd', async () => ({ value: '/tmp/project' }));
  on('session.start', async (_, e) => ({ cwd: e.cwd }));
  on('command.register', async (_, e) => ({ value: { command: e.name } }));
  on('tool.register', async (_, e) => ({ value: { tool: 'mcp__workers__' + e.name } }));
  on('clock.every', async () => new Promise(() => {}));
  on('process.run', async () => ({ value: { exitCode: 0, stdout: '{"url":"http://127.0.0.1:9999","token":"fixture","pid":1}', stderr: '' } }));
  on('http.fetch', async (_, e) => {
    if (e.url.endsWith('/start')) {
      payload = JSON.parse(e.init?.body || '{}');
      return { value: { ok: true, status: 200, headers: {}, text: '{"id":"created"}' } };
    }
    return { value: { ok: true, status: 200, headers: {}, text: '{"workers":[],"agents":[]}' } };
  });
  await $.session.start({ cwd: '/tmp/project', surface: 'terminal', isInteractive: true });
  const ui = await $.ui.mount({ plugin: 'workers', surface: 'terminal', component: 'Pane', requestId: 'workers', props: {} });
  await ui.press({ key: 'new-session' });
  await ui.select({ key: 'launch-harness', value: 'opencode' });
  await ui.select({ key: 'launch-model-opencode', value: '__custom__' });
  await ui.input({ key: 'model', text: 'provider/model', kind: 'change' });
  await ui.input({ key: 'task', text: 'Review this diff', kind: 'change' });
  await ui.input({ key: 'task', text: 'Review this diff', kind: 'submit' });
  expect(payload).toEqual({ kind: 'opencode', task: 'Review this diff', cwd: '/tmp/project', model: 'provider/model', origin: 'direct' });
  await ui.unmount();
});

test('failed bridge startup remains visible with reconnect control', async ($, on) => {
  on('store.get', async () => ({ value: undefined }));
  on('store.set', async () => ({ value: undefined }));
  on('clock.now', async () => ({ value: 2000000 }));
  on('ui.open', async () => ({ value: undefined }));
  on('process.run', async () => ({ value: { exitCode: 1, stdout: '', stderr: 'Python executable unavailable' } }));
  await $.command.run({ command: 'workers' });
  const ui = await $.ui.mount({ plugin: 'workers', surface: 'terminal', component: 'Pane', requestId: 'workers', props: {} });
  expect((await ui.find({ text: 'Python executable unavailable' }))?.text).toContain('Python executable unavailable');
  expect(await ui.find({ key: 'reconnect' })).toBeDefined();
  await ui.unmount();
});


test('empty session launcher uses /new without sending a model prompt', async ($, on) => {
  on('store.get', async () => ({ value: undefined }));
  on('store.set', async () => ({ value: undefined }));
  on('clock.now', async () => ({ value: 2000000 }));
  let endpoint;
  let payload;
  on('session.id', async () => ({ value: 'blank-session' }));
  on('session.cwd', async () => ({ value: '/tmp/project' }));
  on('session.start', async (_, e) => ({ cwd: e.cwd }));
  on('command.register', async (_, e) => ({ value: { command: e.name } }));
  on('tool.register', async (_, e) => ({ value: { tool: 'mcp__workers__' + e.name } }));
  on('clock.every', async () => new Promise(() => {}));
  on('process.run', async () => ({ value: { exitCode: 0, stdout: '{"url":"http://127.0.0.1:9999","token":"fixture","pid":1}', stderr: '' } }));
  on('http.fetch', async (_, e) => {
    if (e.url.endsWith('/context')) return { value: { ok: true, status: 200, headers: {}, text: '{"ok":true}' } };
    if (e.init?.method === 'POST') {
      endpoint = e.url.split('/').pop();
      payload = JSON.parse(e.init.body || '{}');
      return { value: { ok: true, status: 200, headers: {}, text: '{"id":"empty"}' } };
    }
    return { value: { ok: true, status: 200, headers: {}, text: '{"workers":[],"agents":[]}' } };
  });
  await $.session.start({ cwd: '/tmp/project', surface: 'terminal', isInteractive: true });
  const ui = await $.ui.mount({ plugin: 'workers', surface: 'desktop', component: 'Pane', requestId: 'workers', props: { bodyColumns: 90 } });
  await ui.press({ key: 'new-session' });
  await ui.select({ key: 'launch-harness', value: 'qwen' });
  await ui.press({ key: 'start-worker' });
  expect(endpoint).toBe('new');
  expect(payload).toEqual({ kind: 'qwen', cwd: '/tmp/project', origin: 'direct' });
  await ui.unmount();
});

test('Desktop session opens a sidebar pane without taking keyboard focus', async ($, on) => {
  on('store.get', async () => ({ value: undefined }));
  on('store.set', async () => ({ value: undefined }));
  on('clock.now', async () => ({ value: 2000000 }));
  let pane;
  on('session.id', async () => ({ value: 'desktop-session' }));
  on('session.cwd', async () => ({ value: '/tmp/project' }));
  on('session.start', async (_, e) => ({ cwd: e.cwd }));
  on('command.register', async (_, e) => ({ value: { command: e.name } }));
  on('tool.register', async (_, e) => ({ value: { tool: 'mcp__workers__' + e.name } }));
  on('ui.open', async (_, e) => { pane = e; return { value: undefined }; });
  await $.session.start({ cwd: '/tmp/project', surface: 'desktop', isInteractive: true });
  expect(pane.id).toBe('workers');
  expect(pane.focus).toBeUndefined();
});

test('sidebar adapts to narrow panes', async ($, on) => {
  on('store.get', async () => ({ value: undefined }));
  on('store.set', async () => ({ value: undefined }));
  on('clock.now', async () => ({ value: 2000000 }));
  const ui = await $.ui.mount({ plugin: 'workers', surface: 'desktop', component: 'Pane', requestId: 'workers', props: { bodyColumns: 44 } });
  const tree = await ui.drawn();
  expect(tree.props.flexDirection).toBe('column');
  expect(await ui.find({ key: 'session-sidebar' })).toBeDefined();
  expect(await ui.find({ key: 'session-detail' })).toBeUndefined();
  await ui.press({ key: 'new-session' });
  expect(await ui.find({ key: 'session-sidebar' })).toBeUndefined();
  expect(await ui.find({ key: 'session-detail' })).toBeDefined();
  await ui.unmount();
});


test('Workers button opens directly without the slash-command dispatcher', async ($, on) => {
  on('store.get', async () => ({ value: undefined }));
  on('store.set', async () => ({ value: undefined }));
  on('clock.now', async () => ({ value: 2000000 }));
  let opened = false;
  on('ui.render', { component: 'AbovePrompt' }, async ($, e) => { const { Text } = $.ui.resolve(e); return Text({ children: [''] }); });
  on('ui.open', async (_, e) => { opened = e.id === 'workers'; return { value: undefined }; });
  on('clock.after', async () => new Promise(() => {}));
  on('clock.every', async () => new Promise(() => {}));
  on('process.run', async () => ({ value: { exitCode: 0, stdout: '{"url":"http://127.0.0.1:9999","token":"fixture","pid":1}', stderr: '' } }));
  on('http.fetch', async () => ({ value: { ok: true, status: 200, headers: {}, text: '{"workers":[],"agents":[]}' } }));
  const ui = await $.ui.mount({ plugin: 'workers', surface: 'desktop', component: 'AbovePrompt', props: {} });
  await ui.press({ key: 'open-workers' });
  expect(opened).toBe(true);
  await ui.unmount();
});

test('namespaced worker command returns an explicit handled result', async ($, on) => {
  on('store.get', async () => ({ value: undefined }));
  on('store.set', async () => ({ value: undefined }));
  on('clock.now', async () => ({ value: 2000000 }));
  on('ui.open', async () => ({ value: undefined }));
  on('clock.every', async () => new Promise(() => {}));
  on('process.run', async () => ({ value: { exitCode: 0, stdout: '{"url":"http://127.0.0.1:9999","token":"fixture","pid":1}', stderr: '' } }));
  on('http.fetch', async () => ({ value: { ok: true, status: 200, headers: {}, text: '{"workers":[],"agents":[]}' } }));
  const result = await $.command.run({ command: 'workers:workers' });
  expect(result.text).toContain('Workers panel opened');
});
