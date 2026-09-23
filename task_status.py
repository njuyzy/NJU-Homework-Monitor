"""Shared, time-dependent presentation of Moodle submission states."""
from datetime import datetime

from moodle import TZ


def deadline(task):
    try:
        value = datetime.fromisoformat(task.get('due', ''))
        return value if value.tzinfo else value.replace(tzinfo=TZ)
    except (ValueError, TypeError):
        return None


def display_status(task, now=None):
    state = task.get('status', 'unknown')
    due = deadline(task)
    if state in ('pending', 'draft') and due and due <= (now or datetime.now(TZ)):
        return 'overdue'
    return state


def matches_filter(task, mode, now=None):
    now = now or datetime.now(TZ)
    state = display_status(task, now)
    if mode == 'all':
        return True
    if mode == 'pending':
        return state in ('pending', 'draft', 'unknown', 'overdue')
    if mode == 'soon':
        due = deadline(task)
        return state in ('pending', 'draft') and due is not None and 0 < (due - now).total_seconds() <= 72 * 3600
    return state == mode
