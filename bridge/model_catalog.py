#!/usr/bin/env python3
"""Read native CLI model choices without sending a model prompt or exposing config secrets."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess


def read_json(path):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def catalog(kind, workspace):
    models = []
    source = ''
    if kind == 'codex':
        config = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
        data = read_json(config / 'models_cache.json')
        models = [{'id': m['slug'], 'name': m.get('display_name') or m['slug']}
                  for m in data.get('models', []) if isinstance(m, dict) and m.get('slug') and m.get('visibility') != 'hide']
        source = 'Codex local model cache'
    elif kind == 'qwen':
        source = 'Qwen configured model providers'
        for path in [Path.home() / '.qwen/settings.json', Path(workspace) / '.qwen/settings.json']:
            providers = read_json(path).get('modelProviders', {})
            for values in providers.values() if isinstance(providers, dict) else []:
                if isinstance(values, list):
                    models.extend({'id': m['id'], 'name': m.get('name') or m['id']} for m in values if isinstance(m, dict) and m.get('id'))
    elif kind == 'opencode':
        source = 'OpenCode CLI catalog'
        result = subprocess.run(['opencode', 'models'], cwd=workspace, capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise ValueError('OpenCode model catalog is unavailable. Use CLI default or a custom model ID.')
        models = [{'id': line.strip(), 'name': line.strip()} for line in result.stdout.splitlines()
                  if re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_./:+()-]+', line.strip())]
    else:
        raise ValueError('Unknown worker kind')
    distinct = {}
    for m in models:
        mid, name = m.get('id'), m.get('name')
        if isinstance(mid, str) and mid and len(mid) < 200 and not re.search(r'[\x00-\x1f]', mid):
            distinct[mid] = {'id': mid, 'name': str(name)[:100]}
    return {'models': list(distinct.values())[:500], 'source': source}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--kind', required=True, choices=['codex', 'qwen', 'opencode'])
    parser.add_argument('--workspace', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(catalog(args.kind, args.workspace)))
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'models': [], 'error': str(exc)}))
