import json
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
