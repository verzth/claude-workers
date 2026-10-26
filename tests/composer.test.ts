import { test, expect } from 'claude-code/testing';

// The main composer belongs to Claude. Harness and model are chosen in the Workers pane (New session).
function setup(on) {
  const calls = [], workers = [];
  let forwarded = 0;
  on('clock.now', async () => ({ value: 2000000 }));
  on('session.id', async () => ({ value: 'owner' }));
  on('session.cwd', async () => ({ value: '/tmp/project' }));
  on('session.start', async (_, e) => ({ cwd: e.cwd }));
  on('command.register', async (_, e) => ({ value: { command: e.name } }));
  on('tool.register', async (_, e) => ({ value: { tool: 'mcp__workers__' + e.name } }));
  on('ui.open', async () => ({ value: undefined }));
  on('ui.toast', async () => ({ value: undefined }));
  on('clock.every', async () => new Promise(() => {}));
  on('store.get', async () => ({ value: undefined }));
  on('store.set', async () => ({ value: undefined }));
  on('process.run', async (_, e) => ({ value: { exitCode: 0, stderr: '', stdout: e.argv.some(x => x.endsWith('/model_catalog.py')) ?
    '{"models":[{"id":"native/model","name":"Native model"}],"source":"Fixture"}' : '{"url":"http://127.0.0.1:9999","token":"fixture","pid":1}' } }));
  on('http.fetch', async (_, e) => {
    if (e.url.endsWith('/overview')) return { value: { ok: true, status: 200, headers: {}, text: JSON.stringify({ workers, agents: [] }) } };
    if (e.url.endsWith('/start')) {
      const data = JSON.parse(e.init.body); calls.push(data);
      const worker = { id: 'new-' + workers.length, hostSessionId: 'owner', ...data, status: 'running', canMessage: true, reachable: true,
        tools: [], events: [], history: [], approvals: [], queued: 0, output: '' };
      workers.push(worker);
      return { value: { ok: true, status: 200, headers: {}, text: JSON.stringify(worker) } };
    }
    return { value: { ok: true, status: 200, headers: {}, text: '{"ok":true}' } };
  });
  on('ui.render', { component: 'AbovePrompt' }, async ($, e) => { const { Text } = $.ui.resolve(e); return Text({ children: ['Native composer untouched'] }); });
  on('prompt.submit', async (_, e) => { forwarded++; return { text: e.text, origin: e.origin }; });
  return { calls, forwarded: () => forwarded };
}

test('main composer band has only the Workers button, no harness or model controls', async ($, on) => {
  setup(on);
  await $.session.start({ cwd: '/tmp/project' });
  const ui = await $.ui.mount({ plugin: 'workers', surface: 'terminal', component: 'AbovePrompt', props: {} });
  expect((await ui.find({ text: 'Native composer untouched' }))?.text).toContain('Native composer untouched');
  expect(await ui.find({ key: 'open-workers' })).toBeDefined();
  expect(await ui.find({ key: 'composer-harness' })).toBeUndefined();
  expect(await ui.find({ key: 'composer-model' })).toBeUndefined();
  await ui.unmount();
});

test('composer text always reaches Claude and never a worker', async ($, on) => {
  const f = setup(on);
  await $.session.start({ cwd: '/tmp/project' });
  await $.prompt.submit({ text: 'hello', origin: { kind: 'composer' }, wait: false });
  expect(f.forwarded()).toBe(1);
  expect(f.calls).toEqual([]);
});

test('New session picks harness and model from dropdowns and sends them to the bridge', async ($, on) => {
  const f = setup(on);
  await $.session.start({ cwd: '/tmp/project' });
  const ui = await $.ui.mount({ plugin: 'workers', surface: 'terminal', component: 'Pane', requestId: 'workers', props: {} });
  await ui.press({ key: 'new-session' });
  await ui.select({ key: 'launch-harness', value: 'qwen' });
  const model = await ui.find({ key: 'launch-model-qwen' });
  expect(model?.props.options.some(o => o.value === 'native/model')).toBe(true);
  await ui.select({ key: 'launch-model-qwen', value: 'native/model' });
  await ui.input({ key: 'task', text: 'FIRST', kind: 'change' });
  await ui.input({ key: 'task', text: 'FIRST', kind: 'submit' });
  expect(f.calls).toEqual([{ kind: 'qwen', task: 'FIRST', cwd: '/tmp/project', model: 'native/model', origin: 'direct' }]);
  await ui.unmount();
});

test('launcher keeps draft and model when opening options and refreshing the catalog', async ($, on) => {
  const f = setup(on);
  await $.session.start({ cwd: '/tmp/project' });
  const ui = await $.ui.mount({ plugin: 'workers', surface: 'desktop', component: 'Pane', requestId: 'workers', props: { bodyColumns: 44 } });
  await ui.press({ key: 'new-session' });
  await ui.select({ key: 'launch-harness', value: 'qwen' });
  await ui.select({ key: 'launch-model-qwen', value: 'native/model' });
  await ui.input({ key: 'task', text: 'Keep my draft', kind: 'change' });
  expect(await ui.find({ key: 'refresh-models' })).toBeUndefined();
  await ui.press({ key: 'launch-options' });
  await ui.press({ key: 'refresh-models' });
  expect((await ui.find({ key: 'task' }))?.props.value).toBe('Keep my draft');
  expect((await ui.find({ key: 'launch-model-qwen' }))?.props.value).toBe('native/model');
  expect(f.calls).toEqual([]);
  await ui.press({ key: 'launch-options' });
  expect(await ui.find({ key: 'refresh-models' })).toBeUndefined();
  await ui.input({ key: 'task', text: 'Keep my draft', kind: 'submit' });
  expect(f.calls[0].task).toBe('Keep my draft');
  expect(f.calls[0].model).toBe('native/model');
  await ui.unmount();
});
