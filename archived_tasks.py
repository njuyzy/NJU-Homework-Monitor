"""Persistent local archive state for homework tasks."""
from datetime import datetime

import storage


FILE_NAME = 'archived-tasks.json'


def load():
    value = storage.read(FILE_NAME, {})
    return value if isinstance(value, dict) else {}


def is_archived(task, records=None):
    return str(task.get('id')) in (records if records is not None else load())


def archive(task):
    records = load()
    records[str(task.get('id'))] = {'archived_at': datetime.now().astimezone().isoformat()}
    storage.write(FILE_NAME, records)


def visible(tasks):
    records = load()
    return [task for task in tasks if not is_archived(task, records)]
