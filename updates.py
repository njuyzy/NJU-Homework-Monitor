"""Read-only update checks against the public repository; no Git needed in an exe."""
import json
import re
import subprocess
import sys
from pathlib import Path

import requests

REPOSITORY = 'njuyzy/NJU-Homework-Monitor'
WEB_URL = f'https://github.com/{REPOSITORY}'
API_URL = f'https://api.github.com/repos/{REPOSITORY}'
BRANCH = 'main'
ASSET_NAME = 'NJU-Homework-Monitor.exe'


def local_revision():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    try:
        revision = json.loads((root / 'build-info.json').read_text(encoding='utf-8-sig'))['revision']
    except (OSError, ValueError, KeyError, TypeError):
        if getattr(sys, 'frozen', False):
            return None
        try:
            revision = subprocess.check_output(
                ['git', 'rev-parse', 'HEAD'], cwd=root, text=True, timeout=5,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.SubprocessError):
            return None
    return revision if isinstance(revision, str) and re.fullmatch(r'[0-9a-f]{40}', revision) else None


def check_for_update(revision=None):
    revision = revision or local_revision()
    if not revision:
        return None
    with requests.Session() as session:
        session.headers.update({'Accept': 'application/vnd.github+json',
                                'User-Agent': 'NJU-Homework-Monitor-update-check'})

        def get(path):
            response = session.get(API_URL + path, timeout=(5, 10))
            response.raise_for_status()
            return response.json()

        comparison = get(f'/compare/{revision}...{BRANCH}')
        if comparison.get('status') != 'ahead':
            return None
        latest = comparison['commits'][-1]['sha']
        # Read the actual branch tip: compare results can be truncated after 250 commits.
        if comparison.get('total_commits', 0) > len(comparison['commits']):
            latest = get(f'/commits/{BRANCH}')['sha']
        if not re.fullmatch(r'[0-9a-f]{40}', latest):
            raise ValueError('更新版本编号无效')
        result = {'revision': latest, 'ready': False,
                  'url': WEB_URL + '/actions/workflows/release.yml'}
        tag = 'build-' + latest
        response = session.get(API_URL + '/releases/tags/' + tag, timeout=(5, 10))
        if response.status_code == 404:
            return result
        response.raise_for_status()
        release = response.json()
        if not release.get('draft') and any(
                asset.get('name') == ASSET_NAME and asset.get('state') == 'uploaded'
                for asset in release.get('assets', [])):
            result.update(ready=True, url=WEB_URL + '/releases/tag/' + tag)
        return result
