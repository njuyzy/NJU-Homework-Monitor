import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from datetime import datetime, timedelta

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

import desktop_app
import storage


def test_clickable_stats_combined_filters_and_update_button(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    monkeypatch.setattr(desktop_app.backend, 'monitor', lambda: None)
    monkeypatch.setattr(desktop_app.MainWindow, 'check_updates', lambda self: None)
    app = QApplication.instance() or QApplication([])
    now = datetime.now().astimezone()
    tasks = [dict(id=str(i), title=title, course='算法', course_id=course, status=state,
                  due=(now + timedelta(hours=hours)).isoformat())
             for i, (title, course, state, hours) in enumerate([
                 ('逾期作业', '1', 'pending', -1), ('已完成', '1', 'submitted', -1),
                 ('快截止', '2', 'draft', 5), ('待核实', '2', 'unknown', -1)])]
    storage.write('snapshot.json', {'tasks': tasks})
    window = desktop_app.MainWindow()
    window.show()
    app.processEvents()
    for button in window.stat_buttons.values():
        assert button.height() >= 116
        assert all(label.geometry().bottom() < button.height() for label in button.findChildren(QLabel))
    def visible_cards():
        return [window.task_layout.itemAt(i).widget() for i in range(window.task_layout.count())
                if isinstance(window.task_layout.itemAt(i).widget(), desktop_app.TaskCard)]
    assert {key: label.text() for key, label in window.stat_labels.items()} == {
        'pending': '3', 'soon': '1', 'overdue': '1', 'submitted': '1'}
    for mode in ('overdue', 'submitted', 'soon', 'pending'):
        window.stat_buttons[mode].click()
        assert window.task_filter.currentData() == mode
        assert window.stat_buttons[mode].isChecked()
        assert len(visible_cards()) == int(window.stat_labels[mode].text())
    window.stat_buttons['overdue'].click()
    assert '已逾期' in [label.text() for label in visible_cards()[0].findChildren(QLabel)]
    window.task_search.setText('不存在')
    assert not visible_cards()
    window.task_search.clear()
    window.course_filter.setCurrentIndex(window.course_filter.findData('2'))
    assert not visible_cards()
    window.course_filter.setCurrentIndex(0)
    window.task_filter.setCurrentIndex(window.task_filter.findData('all'))
    assert len(visible_cards()) == 4
    # Crossing the deadline must rerender even with an unchanged snapshot and all filter.
    tasks[2]['due'] = (now - timedelta(seconds=1)).isoformat()
    window.snapshot['tasks'] = tasks
    window.render_tasks()
    overdue_card = visible_cards()[2]
    assert {'已逾期', '草稿'} <= {label.text() for label in overdue_card.findChildren(QLabel)}
    assert window.update_button.isHidden()
    window.show_update({'ready': False, 'url': 'https://github.com'})
    assert not window.update_button.isHidden() and '打包中' in window.update_button.text()
    window.show_update({'ready': True, 'url': 'https://github.com'})
    assert window.update_button.text() == '↑ 更新可用'
    window.show_update(None)
    assert window.update_button.isHidden()
    window.close()
    app.processEvents()


def test_failed_update_check_can_retry_without_losing_visible_update(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    monkeypatch.setattr(desktop_app.backend, 'monitor', lambda: None)
    app = QApplication.instance() or QApplication([])
    def offline():
        raise TimeoutError('offline')
    monkeypatch.setattr(desktop_app.updates, 'check_for_update', offline)
    window = desktop_app.MainWindow()
    # Execute workers synchronously to test both signal paths deterministically.
    from types import SimpleNamespace
    window.thread_pool = SimpleNamespace(start=lambda worker: worker.run())
    window.show_update({'ready': True, 'url': 'https://github.com'})
    window.check_updates()
    assert not window.checking_updates and not window.active_workers
    assert not window.update_button.isHidden()
    monkeypatch.setattr(desktop_app.updates, 'check_for_update', lambda: None)
    window.check_updates()
    assert not window.checking_updates and window.update_button.isHidden()
    window.close()
    app.processEvents()
