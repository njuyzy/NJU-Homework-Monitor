import archived_tasks
import storage


def test_archived_tasks_are_persistent_and_hidden(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    first = {'id': '1', 'title': '归档任务'}
    second = {'id': '2', 'title': '保留任务'}

    archived_tasks.archive(first)

    assert archived_tasks.is_archived(first)
    assert archived_tasks.visible([first, second]) == [second]
