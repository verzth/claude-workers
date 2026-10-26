import { test, expect } from 'claude-code/testing';

function fixture(on) {
  const saved = new Map();
  const calls = [];
  const tools = Array.from({ length: 8 }, (_, i) => ({ id: 'tool' + i, title: i === 7 ? 'ssh host ' + 'long command '.repeat(25) : 'Read file ' + i,
    status: i === 7 ? 'in_progress' : 'completed', detail: JSON.stringify([{ type: 'content', content: { type: 'text', text: 'Readable output ' + i + '\nnext line' } }]) }));
  const workers = [{ id: 'one', hostSessionId: 'owner', parentTitle: 'Maintenance', origin: 'delegated',
    kind: 'opencode', task: 'Review the deployment', cwd: '/tmp/project', model: 'native/model', status: 'running',
    canMessage: true, reachable: true, tools, events: [{ kind: 'tool', text: 'DUPLICATE_TOOL_EVENT' }, { kind: 'log', text: 'RAW_DIAGNOSTIC' }],
    history: [], approvals: [], queued: 0, output: '' }];
  on('clock.now', async () => ({ value: 2000000 }));
  on('session.id', async () => ({ value: 'owner' }));
  on('session.cwd', async () => ({ value: '/tmp/project' }));
  on('session.start', async (_, e) => ({ cwd: e.cwd }));
  on('command.register', async (_, e) => ({ value: { command: e.name } }));
  on('tool.register', async (_, e) => ({ value: { tool: 'mcp__workers__' + e.name } }));
  on('ui.open', async () => ({ value: undefined }));
  on('ui.toast', async () => ({ value: undefined }));
  on('clock.every', async () => new Promise(() => {}));
  on('store.get', async (_, e) => ({ value: saved.get(e.key) }));
  on('store.set', async (_, e) => { saved.set(e.key, e.value); return { value: undefined }; });
  on('process.run', async (_, e) => {
    if (e.argv.includes('retry-worker')) {
      calls.push('retry');
      const next = { ...workers[0], id: 'retried', status: 'starting' };
      workers.push(next);
      return { value: { exitCode: 0, stdout: JSON.stringify({ worker: next }), stderr: '' } };
    }
    if (e.argv.includes('delete-worker')) {
      calls.push('delete');
      return { value: { exitCode: 0, stdout: '{"ok":true}', stderr: '' } };
    }
    return { value: { exitCode: 0, stdout: '{"url":"http://127.0.0.1:9999","token":"fixture","pid":1}', stderr: '' } };
  });
  on('http.fetch', async (_, e) => {
    if (e.url.endsWith('/overview')) return { value: { ok: true, status: 200, headers: {}, text: JSON.stringify({ workers, agents: [] }) } };
    return { value: { ok: true, status: 200, headers: {}, text: '{"ok":true}' } };
  });
  return { workers, calls, saved };
}
async function pane($) {
  await $.session.start({ cwd: '/tmp/project' });
  await $.command.run({ command: 'workers' });
  return $.ui.mount({ plugin: 'workers', surface: 'desktop', component: 'Pane', requestId: 'workers', props: { bodyColumns: 72 } });
}

test('activity is compact, expandable, decoded and does not duplicate tool events', async ($, on) => {
  fixture(on);
  const ui = await pane($);
  await ui.press({ key: 'pick-one' });
  expect((await ui.findAll({ type: 'Box' })).filter(x => x.props.key?.startsWith('activity-')).length).toBe(5);
  expect(await ui.find({ type: 'Code' })).toBeUndefined();
  expect(await ui.find({ text: 'DUPLICATE_TOOL_EVENT' })).toBeUndefined();
  expect(await ui.find({ text: 'RAW_DIAGNOSTIC' })).toBeUndefined();
  expect((await ui.find({ key: 'tool-tool7' }))?.text).toBe('Shell command');
  await ui.press({ key: 'tool-tool7' });
  expect((await ui.find({ type: 'Code' }))?.props.source).toBe('Readable output 7\nnext line');
  await ui.press({ key: 'tool-tool7' });
  expect(await ui.find({ type: 'Code' })).toBeUndefined();
  await ui.press({ key: 'older-tools' });
  expect((await ui.findAll({ type: 'Box' })).filter(x => x.props.key?.startsWith('activity-')).length).toBe(8);
  await ui.press({ key: 'diagnostics' });
  expect((await ui.find({ type: 'Code' }))?.props.source).toBe('RAW_DIAGNOSTIC');
  expect(await ui.find({ key: 'send-message' })).toBeUndefined();
  expect((await ui.findAll({ type: 'Input' })).length).toBe(1);
  await ui.unmount();
});

test('archive hides from normal views, Archived can restore, metadata survives refresh', async ($, on) => {
  const { saved } = fixture(on);
  const ui = await pane($);
  await ui.press({ key: 'pick-one' });
  await ui.press({ key: 'worker-actions' });
  await ui.press({ key: 'archive-worker' });
  expect(await ui.find({ key: 'pick-one' })).toBeUndefined();
  expect(saved.get('worker-view:owner:one').archived).toBe(true);
  await ui.press({ key: 'filter-all' });
  expect(await ui.find({ key: 'pick-one' })).toBeUndefined();
  await ui.press({ key: 'filter-archived' });
  expect(await ui.find({ key: 'pick-one' })).toBeDefined();
  await ui.press({ key: 'pick-one' });
  await ui.press({ key: 'worker-actions' });
  expect((await ui.find({ key: 'archive-worker' }))?.text).toBe('Unarchive');
  await ui.press({ key: 'archive-worker' });
  await ui.press({ key: 'filter-session' });
  expect(await ui.find({ key: 'pick-one' })).toBeDefined();
  await ui.unmount();
});

test('delete confirms before stopping worker and deleted record stays hidden', async ($, on) => {
  const { calls, saved } = fixture(on);
  const ui = await pane($);
  await ui.press({ key: 'pick-one' });
  await ui.press({ key: 'worker-actions' });
  await ui.press({ key: 'delete-worker' });
  expect(calls).toEqual([]);
  await ui.press({ key: 'cancel-delete' });
  expect(calls).toEqual([]);
  await ui.press({ key: 'delete-worker' });
  await ui.press({ key: 'confirm-delete' });
  expect(calls).toEqual(['delete']);
  expect(saved.get('worker-view:owner:one').deleted).toBe(true);
  await ui.press({ key: 'filter-archived' });
  expect(await ui.find({ key: 'pick-one' })).toBeUndefined();
  await ui.unmount();
});

test('retry failed selects the new attempt and archives the source', async ($, on) => {
  const { workers, calls, saved } = fixture(on);
  workers[0].status = 'error';
  const ui = await pane($);
  await ui.press({ key: 'retry-one' });
  expect(calls).toEqual(['retry']);
  expect(saved.get('worker-view:owner:one').archived).toBe(true);
  expect(await ui.find({ key: 'message-retried' })).toBeDefined();
  await ui.press({ key: 'back-overview' });
  expect(await ui.find({ key: 'pick-one' })).toBeUndefined();
  expect(await ui.find({ key: 'pick-retried' })).toBeDefined();
  await ui.unmount();
});
