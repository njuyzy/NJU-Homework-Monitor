from datetime import datetime, timedelta

import pytest

from moodle import TZ
from task_status import display_status, matches_filter

NOW = datetime(2026, 9, 23, 12, tzinfo=TZ)


@pytest.mark.parametrize('state,expected', [
    ('pending', 'overdue'), ('draft', 'overdue'),
    ('submitted', 'submitted'), ('unknown', 'unknown'),
])
def test_overdue_preserves_submission_state(state, expected):
    task = {'status': state, 'due': NOW.isoformat()}
    assert display_status(task, NOW) == expected
    assert task['status'] == state


@pytest.mark.parametrize('hours,soon,overdue', [(-1, False, True), (0, False, True),
                                                         (1, True, False), (72, True, False), (73, False, False)])
def test_filter_deadline_boundaries(hours, soon, overdue):
    task = {'status': 'pending', 'due': (NOW + timedelta(hours=hours)).isoformat()}
    assert matches_filter(task, 'soon', NOW) == soon
    assert matches_filter(task, 'overdue', NOW) == overdue
    assert matches_filter(task, 'pending', NOW)


@pytest.mark.parametrize('due', [None, '', 'unrecognized'])
def test_missing_or_bad_date(due):
    task = {'status': 'draft', 'due': due}
    assert display_status(task, NOW) == 'draft'
    assert not matches_filter(task, 'soon', NOW)


def test_naive_date_is_beijing_and_unknown_is_pending():
    assert display_status({'status': 'pending', 'due': '2026-09-23T12:00:00'}, NOW) == 'overdue'
    assert matches_filter({'status': 'unknown'}, 'pending', NOW)
    assert not matches_filter({'status': 'unknown', 'due': NOW.isoformat()}, 'overdue', NOW)
