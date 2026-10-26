import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('models', Path(__file__).resolve().parents[1] / 'bridge/model_catalog.py')
models = importlib.util.module_from_spec(spec)
spec.loader.exec_module(models)


class ModelCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = patch.object(Path, 'home', return_value=self.root)
        self.home.start()
        self.env = patch.dict(os.environ, {'CODEX_HOME': str(self.root / '.codex')})
        self.env.start()

    def tearDown(self):
        self.env.stop(); self.home.stop(); self.tmp.cleanup()

    def test_codex_cache_filters_hidden_entries_and_never_returns_identity(self):
        path = self.root / '.codex/models_cache.json'; path.parent.mkdir()
        path.write_text(json.dumps({'identity': 'private-account', 'models': [
            {'slug': 'visible', 'display_name': 'Visible model'}, {'slug': 'hidden', 'visibility': 'hide'}]}))
        result = models.catalog('codex', self.root)
        self.assertEqual(result['models'], [{'id': 'visible', 'name': 'Visible model'}])
        self.assertNotIn('private-account', json.dumps(result))

    def test_qwen_only_extracts_model_ids_and_names_from_configuration(self):
        path = self.root / '.qwen/settings.json'; path.parent.mkdir()
        path.write_text(json.dumps({'env': {'API_KEY': 'SECRET'}, 'modelProviders': {'openai': [
            {'id': 'native/model', 'name': 'Native model', 'apiKey': 'SECRET', 'baseUrl': 'https://private.invalid'}]}}))
        result = models.catalog('qwen', self.root)
        self.assertEqual(result['models'], [{'id': 'native/model', 'name': 'Native model'}])
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('private.invalid', json.dumps(result))

    def test_opencode_filters_output_to_provider_model_ids(self):
        with patch.object(models.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='log message\nprovider/model\nprovider/model\nhttps://private.invalid/key\n')):
            result = models.catalog('opencode', self.root)
        self.assertEqual(result['models'], [{'id': 'provider/model', 'name': 'provider/model'}])

    def test_missing_or_invalid_cache_returns_no_invented_models(self):
        self.assertEqual(models.catalog('codex', self.root)['models'], [])
        path = self.root / '.codex/models_cache.json'; path.parent.mkdir(); path.write_text('[]')
        self.assertEqual(models.catalog('codex', self.root)['models'], [])
