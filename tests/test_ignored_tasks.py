from datetime import datetime, timedelta

import ignored_tasks
import storage
from moodle import TZ


def test_ignore_state_is_persistent_and_expires_when_deadline_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    task = {'id': '42', 'due': datetime.now(TZ).isoformat()}
    ignored_tasks.set_ignored(task)
    assert ignored_tasks.is_ignored(task)

    changed = task | {'due': (datetime.now(TZ) + timedelta(days=1)).isoformat()}
    assert not ignored_tasks.is_ignored(changed)

    ignored_tasks.set_ignored(task, False)
    assert not ignored_tasks.is_ignored(task)
