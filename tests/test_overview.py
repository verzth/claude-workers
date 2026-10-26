"""Integration checks for multi-parent monitoring and control boundaries."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from test_bridge import bridge


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / 'project'
        self.workspace.mkdir()
        self.registry = self.root / 'runtime'
        self.registry.mkdir(mode=0o700)
        self.path_patch = patch.object(bridge, 'runtime_path', side_effect=lambda sid: self.registry / (sid + '.json'))
        self.path_patch.start()
        self.hosts = []

    def tearDown(self):
        for manager, server, thread in self.hosts:
            server.shutdown()
            server.server_close()
            thread.join()
            manager.close()
        self.path_patch.stop()
        self.tmp.cleanup()

    def host(self, sid, workspace=None):
        manager = bridge.Manager(sid)
        manager.workspace = str(workspace or self.workspace)
        manager.title = 'Title for ' + sid
        server = ThreadingHTTPServer(('127.0.0.1', 0), bridge.make_handler(manager, sid + '-token'))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        conn = {'url': f'http://127.0.0.1:{server.server_port}', 'token': sid + '-token',
                'sessionId': sid, 'workspace': manager.workspace, 'pid': os.getpid()}
        bridge.write_connection(self.registry / (sid + '.json'), conn)
        self.hosts.append((manager, server, thread))
        return manager, conn

    def worker(self, manager, origin='delegated'):
        w = bridge.Worker('qwen', str(self.workspace), None, origin=origin)
        w.status, w.session_id = 'idle', 'native-fixture'
        manager.workers[w.id] = w
        return w

    def test_overview_joins_same_workspace_with_parent_metadata_without_tokens(self):
        a, _ = self.host('parent-a')
        b, _ = self.host('parent-b')
        foreign, _ = self.host('outside', self.root)
        self.worker(a)
        self.worker(b, 'direct')
        self.worker(foreign)
        result = a.overview()
        self.assertEqual({w['hostSessionId'] for w in result['workers']}, {'parent-a', 'parent-b'})
        self.assertEqual({w['origin'] for w in result['workers']}, {'delegated', 'direct'})
        self.assertTrue(all(w['reachable'] for w in result['workers']))
        self.assertNotIn('token', json.dumps(result))
        self.assertEqual(len(a.snapshot()['workers']), 1)  # tool-facing state stays scoped

    def test_monitor_does_not_extend_parent_watchdog(self):
        manager, conn = self.host('parent')
        manager.last_seen = time.monotonic() - 90
        before = manager.last_seen
        bridge.connection_request(conn, '/monitor')
        self.assertEqual(manager.last_seen, before)
        bridge.connection_request(conn, '/state')
        self.assertGreater(manager.last_seen, before)

    def test_lost_parent_keeps_last_seen_record_offline_and_disables_controls(self):
        a, _ = self.host('parent-a')
        b, _ = self.host('parent-b')
        w = self.worker(b)
        self.assertEqual(a.overview()['workers'][0]['id'], w.id)
        (self.registry / 'parent-b.json').unlink()
        snapshot = a.overview()['workers'][0]
        self.assertEqual(snapshot['status'], 'offline')
        self.assertFalse(snapshot['reachable'])
        self.assertFalse(snapshot['canMessage'])
        with self.assertRaisesRegex(ValueError, 'offline'):
            a.control({'hostSessionId': b.session, 'action': 'send', 'id': w.id, 'message': 'no'})

    def test_ui_control_routes_to_correct_parent_but_rejects_outside_workspace(self):
        a, _ = self.host('parent-a')
        b, _ = self.host('parent-b')
        outside, _ = self.host('outside', self.root)
        w = self.worker(b)
        foreign = self.worker(outside)
        result = a.control({'hostSessionId': b.session, 'action': 'send', 'id': w.id, 'message': 'Correct parent'})
        self.assertEqual(result, {'delivery': 'queued'})
        self.assertEqual(list(w.messages), ['Correct parent'])
        with self.assertRaisesRegex(ValueError, 'outside'):
            a.control({'hostSessionId': outside.session, 'action': 'send', 'id': foreign.id, 'message': 'no'})
        with self.assertRaisesRegex(ValueError, 'does not belong'):
            a.worker_action('interrupt', {'id': w.id})
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            a.control({'hostSessionId': b.session, 'action': 'shutdown'})

    def test_registry_ignores_unsafe_descriptors(self):
        a, _ = self.host('parent-a')
        b, _ = self.host('parent-b')
        self.worker(b)
        (self.registry / 'parent-b.json').chmod(0o644)
        self.assertEqual(a.overview()['workers'], [])
        (self.registry / 'parent-b.json').chmod(0o600)
        conn = json.loads((self.registry / 'parent-b.json').read_text())
        conn['url'] = 'https://example.com'
        bridge.write_connection(self.registry / 'parent-b.json', conn)
        self.assertEqual(a.peer_connections(), [])

    def test_origin_is_retained_in_worker_snapshot(self):
        manager = bridge.Manager('parent')
        with patch.object(bridge.Worker, 'start'):
            w = manager.start({'kind': 'codex', 'cwd': str(self.workspace), 'origin': 'direct'}, empty=True)
        self.assertEqual(w['origin'], 'direct')
        self.assertEqual(manager.snapshot()['workers'][0]['hostSessionId'], 'parent')
        with self.assertRaisesRegex(ValueError, 'Invalid worker origin'):
            manager.start({'kind': 'codex', 'cwd': str(self.workspace), 'origin': 'fake'}, empty=True)
        manager.close()

    def test_title_uses_actual_custom_title_and_has_stable_fallback(self):
        folder = self.root / '.claude/projects/-tmp-project'
        folder.mkdir(parents=True)
        (folder / 'fixture.jsonl').write_text('\n'.join(json.dumps(x) for x in [
            {'type': 'user', 'message': {'content': 'Original prompt'}},
            {'type': 'custom-title', 'customTitle': 'Worker UI redesign'},
        ]))
        with patch.object(Path, 'home', return_value=self.root):
            self.assertEqual(bridge.session_title('fixture', '/tmp/project'), 'Worker UI redesign')
            self.assertEqual(bridge.session_title('missing', '/tmp/project'), 'Claude session missing')
            self.assertEqual(bridge.session_title('../fixture', '/tmp/project'), 'Claude session ../fixtu')

    def test_delete_stops_and_removes_only_selected_worker(self):
        a, _ = self.host('parent-a')
        target = self.worker(a)
        keep = self.worker(a)
        result = a.worker_action('delete', {'id': target.id})
        self.assertTrue(result['removed'])
        self.assertTrue(target.closed)
        self.assertNotIn(target.id, a.workers)
        self.assertIn(keep.id, a.workers)
        self.assertFalse(keep.closed)

    def test_retry_failed_uses_same_task_kind_folder_model_and_origin(self):
        a, _ = self.host('parent-a')
        w = self.worker(a)
        w.status, w.task, w.model = 'error', 'Original task', 'native/model'
        with patch.object(bridge.Worker, 'start'):
            retry = a.worker_action('retry', {'id': w.id})
        self.assertNotEqual(retry['id'], w.id)
        self.assertEqual(retry['task'], w.task)
        self.assertEqual(retry['kind'], w.kind)
        self.assertEqual(retry['cwd'], w.cwd)
        self.assertEqual(retry['model'], w.model)
        self.assertEqual(retry['origin'], w.origin)
        keep = self.worker(a)
        with self.assertRaisesRegex(ValueError, 'Only failed'):
            a.worker_action('retry', {'id': keep.id})

    def test_management_helper_scopes_cross_parent_and_deletes_without_restarting(self):
        a, _ = self.host('parent-a')
        b, _ = self.host('parent-b')
        other, _ = self.host('outside', self.root)
        w = self.worker(b)
        result = bridge.manage_existing(a.session, b.session, w.id, 'delete')
        self.assertTrue(result['ok'])
        self.assertNotIn(w.id, b.workers)
        foreign = self.worker(other)
        with self.assertRaisesRegex(ValueError, 'outside'):
            bridge.manage_existing(a.session, other.session, foreign.id, 'delete')
        self.assertIn(foreign.id, other.workers)

    def test_legacy_retry_fallback_reuses_start_route_and_delete_closes(self):
        from urllib.error import HTTPError
        a, conn = self.host('parent-a')
        w = self.worker(a)
        w.status, w.task = 'error', 'Original task'
        requests = []
        def request(_, route='/health', body=None, timeout=2):
            requests.append((route, body))
            if route == '/state':
                return a.snapshot()
            if route in {'/retry', '/delete'}:
                raise HTTPError(conn['url'] + route, 404, 'Unknown endpoint', {}, None)
            if route == '/start':
                return {'id': 'retry-fixture', **body}
            if route == '/close':
                return {'ok': True}
            raise AssertionError('Unexpected route')
        with patch.object(bridge, 'connection_request', side_effect=request):
            result = bridge.manage_existing(a.session, a.session, w.id, 'retry')
            self.assertEqual(result['worker']['hostSessionId'], a.session)
            self.assertEqual(result['worker']['task'], w.task)
            self.assertEqual(requests[-1][0], '/start')
            bridge.manage_existing(a.session, a.session, w.id, 'delete')
            self.assertEqual(requests[-1], ('/close', {'id': w.id}))
        self.assertFalse(w.closed)  # mock made no lifecycle changes
