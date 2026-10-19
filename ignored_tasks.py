"""Persistent, deadline-aware ignore state for overdue tasks."""
from datetime import datetime

import storage


FILE_NAME = 'ignored-tasks.json'


def load():
    value = storage.read(FILE_NAME, {})
    return value if isinstance(value, dict) else {}


def is_ignored(task, records=None):
    record = (records if records is not None else load()).get(str(task.get('id')))
    return isinstance(record, dict) and record.get('due') == task.get('due')


def set_ignored(task, ignored=True):
    records = load()
    key = str(task.get('id'))
    if ignored:
        records[key] = {'due': task.get('due'), 'ignored_at': datetime.now().astimezone().isoformat()}
    else:
        records.pop(key, None)
    storage.write(FILE_NAME, records)


def annotate(tasks):
    records = load()
    for task in tasks:
        task['ignored'] = is_ignored(task, records)
    return tasks
