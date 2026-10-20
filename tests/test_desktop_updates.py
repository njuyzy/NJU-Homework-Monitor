import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

import ai_tasks
import desktop_app
import storage


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    monkeypatch.setattr(desktop_app.backend, 'monitor', lambda: None)
    monkeypatch.setattr(desktop_app.MainWindow, 'check_updates', lambda self: None)
    monkeypatch.setattr(desktop_app, 'bridge', ai_tasks.TaskManager())
    monkeypatch.setattr(desktop_app.updates, 'can_auto_update', lambda: True)
    app = QApplication.instance() or QApplication([])
    widget = desktop_app.MainWindow()
    widget.thread_pool = SimpleNamespace(start=lambda worker: worker.run())
    widget.show_update({'ready': True, 'revision': 'b' * 40, 'url': 'https://github.com'})
    yield widget
    widget.close()
    app.processEvents()


def test_update_button_downloads_then_hands_over_before_closing(window, tmp_path, monkeypatch):
    events = []
    work = tmp_path / 'update'
    work.mkdir()
    package = {'work_dir': str(work)}
    def download(info, target, progress, cancel):
        events.append('download')
        assert not window.update_button.isEnabled()
        progress(50, 100)
        assert '50%' in window.update_button.text()
        # A concurrent update check cannot reset the progress UI.
        window.show_update(None)
        assert '50%' in window.update_button.text()
        return package
    monkeypatch.setattr(desktop_app.updates, 'download_update', download)
    monkeypatch.setattr(desktop_app.updates, 'launch_installer', lambda *a: events.append('helper-ready'))
    real_close = window.close
    monkeypatch.setattr(window, 'close', lambda: events.append('close'))
    window.open_update()
    assert events == ['download', 'helper-ready', 'close']
    assert window._restarting_for_update and not window.active_workers
    real_close()


def test_failed_download_leaves_application_open_and_retry_enabled(window, monkeypatch):
    def fail(*args): raise TimeoutError('network timeout')
    monkeypatch.setattr(desktop_app.updates, 'download_update', fail)
    monkeypatch.setattr(desktop_app.updates, 'launch_installer', lambda *a: pytest.fail('Must not install'))
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: errors.append(args[-1]))
    window.open_update()
    assert errors == ['network timeout']
    assert not window._closing and not window.downloading_update and not window.active_workers
    assert window.update_button.isEnabled() and window.update_button.text() == '↑ 更新可用'


def test_manual_close_cancels_pending_download_and_handover(window, tmp_path):
    queued = []
    window.thread_pool = SimpleNamespace(start=queued.append)
    window.open_update()
    cancel = window._update_cancel
    work = tmp_path / 'pending'
    work.mkdir()
    window._update_package = {'work_dir': str(work)}
    window.close()
    assert cancel.is_set() and (work / 'abort').is_file()
    assert len(queued) == 1
