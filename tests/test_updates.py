import json
import hashlib
import struct
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

import updates

OLD = 'a' * 40
NEW = 'b' * 40


def fake_api(monkeypatch, status='ahead', ready=True, missing=False, fail=False, truncated=False):
    calls = []
    class Session:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, **kwargs):
            calls.append(url)
            assert kwargs['timeout'] == (5, 10)
            if fail:
                raise requests.Timeout('offline')
            if '/compare/' in url:
                value = {'status': status, 'commits': [{'sha': OLD if truncated else NEW}],
                         'total_commits': 300 if truncated else 1}
            elif '/commits/' in url:
                value = {'sha': NEW}
            else:
                value = {'draft': False, 'assets': [{'name': updates.ASSET_NAME, 'state': 'uploaded'}] if ready else []}
            return SimpleNamespace(status_code=404 if missing and '/releases/' in url else 200,
                                   json=lambda: value, raise_for_status=lambda: None)
    monkeypatch.setattr(updates.requests, 'Session', Session)
    return calls


@pytest.mark.parametrize('status', ['identical', 'behind', 'diverged'])
def test_no_upgrade_for_identical_or_older_remote(monkeypatch, status):
    calls = fake_api(monkeypatch, status=status)
    assert updates.check_for_update(OLD) is None
    assert len(calls) == 1


@pytest.mark.parametrize('ready,missing', [(True, False), (False, False), (False, True)])
def test_new_push_and_release_readiness(monkeypatch, ready, missing):
    fake_api(monkeypatch, ready=ready, missing=missing)
    result = updates.check_for_update(OLD)
    assert result['revision'] == NEW
    assert result['ready'] == ready
    assert result['url'] == (updates.WEB_URL + '/releases/tag/build-' + NEW if ready
                             else updates.WEB_URL + '/actions/workflows/release.yml')


def test_truncated_compare_uses_actual_tip(monkeypatch):
    calls = fake_api(monkeypatch, truncated=True)
    assert updates.check_for_update(OLD)['revision'] == NEW
    assert any('/commits/main' in url for url in calls)


def test_offline_and_missing_build_info(monkeypatch, tmp_path):
    fake_api(monkeypatch, fail=True)
    with pytest.raises(requests.Timeout):
        updates.check_for_update(OLD)
    monkeypatch.setattr(updates.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(updates.sys, '_MEIPASS', str(tmp_path), raising=False)
    assert updates.check_for_update() is None
    (tmp_path / 'build-info.json').write_text(json.dumps({'revision': OLD}), encoding='utf-8-sig')
    assert updates.local_revision() == OLD


def executable_bytes():
    header = bytearray(128)
    header[:2] = b'MZ'
    struct.pack_into('<I', header, 60, 64)
    header[64:68] = b'PE\0\0'
    return bytes(header)


def release_info(content):
    return {'revision': NEW, 'ready': True, 'url': 'https://untrusted.invalid/program.exe',
            'asset': {'name': updates.ASSET_NAME, 'size': len(content),
                      'digest': 'sha256:' + hashlib.sha256(content).hexdigest()}}


def fake_download(monkeypatch, chunks):
    calls = []
    class Download:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, *args):
            for chunk in chunks:
                if isinstance(chunk, Exception): raise chunk
                if callable(chunk): chunk(); continue
                yield chunk
    class Session:
        def __init__(self): self.headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, **kwargs):
            calls.append(url)
            assert kwargs['stream'] is True
            return Download()
    monkeypatch.setattr(updates.requests, 'Session', Session)
    return calls


def test_verified_download_is_staged_without_replacing_installed_exe(tmp_path, monkeypatch):
    content = executable_bytes()
    target = tmp_path / 'Desktop app.exe'
    target.write_bytes(b'old application')
    calls = fake_download(monkeypatch, [content[:50], content[50:]])
    progress = []
    package = updates.download_update(release_info(content), target, lambda *args: progress.append(args))
    assert target.read_bytes() == b'old application'
    assert Path(package['staged']).read_bytes() == content
    assert calls == [f'{updates.WEB_URL}/releases/download/build-{NEW}/{updates.ASSET_NAME}']
    assert progress[-1] == (len(content), len(content))
    updates.cleanup_download(package)
    assert not Path(package['work_dir']).exists()


@pytest.mark.parametrize('failure', ['truncated', 'oversize', 'checksum', 'not_exe', 'offline', 'cancel'])
def test_failed_download_keeps_installed_exe_and_removes_partial_file(tmp_path, monkeypatch, failure):
    content = executable_bytes()
    info = release_info(content)
    cancel = threading.Event()
    chunks = [content]
    if failure == 'truncated': chunks = [content[:20]]
    elif failure == 'oversize': chunks = [content + b'x']
    elif failure == 'checksum': chunks = [content[:-1] + b'x']
    elif failure == 'not_exe':
        chunks = [b'x' * 128]
        info = release_info(chunks[0])
    elif failure == 'offline': chunks = [content[:20], requests.ConnectionError('offline')]
    elif failure == 'cancel': chunks = [content[:20], cancel.set, content[20:]]
    target = tmp_path / 'app.exe'
    target.write_bytes(b'old')
    fake_download(monkeypatch, chunks)
    with pytest.raises((ValueError, requests.ConnectionError, updates.UpdateCancelled)):
        updates.download_update(info, target, cancel=cancel)
    assert target.read_bytes() == b'old'
    assert not list(tmp_path.glob('.nju-update-*'))


def test_missing_digest_is_rejected_before_network_access(tmp_path, monkeypatch):
    info = release_info(executable_bytes())
    info['asset']['digest'] = None
    monkeypatch.setattr(updates.requests, 'Session', lambda: pytest.fail('Must not download an unverifiable update'))
    with pytest.raises(ValueError, match='SHA-256'):
        updates.download_update(info, tmp_path / 'app.exe')


def test_helper_acknowledgement_and_safe_path_arguments(tmp_path, monkeypatch):
    content = executable_bytes()
    target = tmp_path / "app with spaces &'().exe"
    target.write_bytes(b'old')
    fake_download(monkeypatch, [content])
    package = updates.download_update(release_info(content), target)
    seen = []
    def start(command, **kwargs):
        seen.append((command, kwargs))
        Path(package['work_dir'], 'ready').touch()
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(updates.subprocess, 'Popen', start)
    updates.launch_installer(package, tmp_path / 'data/update.log')
    plan = json.loads(Path(package['work_dir'], 'plan.json').read_text(encoding='utf-8'))
    assert plan['target'] == str(target.resolve())
    assert plan['sha256'] == hashlib.sha256(content).hexdigest()
    command, kwargs = seen[0]
    assert '-File' in command and '-PlanPath' in command
    assert kwargs.get('shell', False) is False
    assert target.read_bytes() == b'old'


def test_failed_helper_start_never_requests_old_application_shutdown(tmp_path, monkeypatch):
    content = executable_bytes()
    target = tmp_path / 'app.exe'
    target.write_bytes(b'old')
    fake_download(monkeypatch, [content])
    package = updates.download_update(release_info(content), target)
    monkeypatch.setattr(updates.subprocess, 'Popen', lambda *a, **k: SimpleNamespace(poll=lambda: 1))
    with pytest.raises(RuntimeError, match='无法启动'):
        updates.launch_installer(package, tmp_path / 'data/update.log')
    assert Path(package['work_dir'], 'abort').is_file()
    assert target.read_bytes() == b'old'


def test_restart_acknowledgement_is_scoped_to_the_installation(tmp_path, monkeypatch):
    target = tmp_path / 'app.exe'
    monkeypatch.setattr(updates.sys, 'executable', str(target))
    work = tmp_path / '.nju-update-test'
    work.mkdir()
    path = work / 'restarted'
    argv = ['app.exe', '--update-ready', str(path)]
    assert updates.consume_restart_argument(argv) == str(path)
    assert argv == ['app.exe']
    updates.mark_restart_ready(path)
    assert path.read_text() == 'ready'
    with pytest.raises(ValueError):
        updates.mark_restart_ready(tmp_path / 'private.txt')
