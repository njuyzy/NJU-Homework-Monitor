"""Offscreen smoke test for task controls and the model configuration dialog."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLineEdit, QPushButton

import storage
import ai_tasks
import desktop_app


def test_code_worker_errors_return_failure_without_gui_dialog(monkeypatch, capsys):
    import code_runner
    monkeypatch.setattr(desktop_app, '_install_excepthook', lambda: None)
    monkeypatch.setattr(desktop_app.sys, 'argv', ['app.exe', '--code-worker', 'workspace', 'source.py'])
    def fail(args):
        raise ModuleNotFoundError('missing-test-library')
    monkeypatch.setattr(code_runner, 'main', fail)
    assert desktop_app.main() == 1
    assert 'missing-test-library' in capsys.readouterr().err


def test_ai_page_and_model_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    monkeypatch.setattr(desktop_app.backend, 'monitor', lambda: None)
    monkeypatch.setattr(desktop_app.MainWindow, 'check_updates', lambda self: None)
    manager = ai_tasks.TaskManager()
    monkeypatch.setattr(desktop_app, 'bridge', manager)
    app = QApplication.instance() or QApplication([])
    window = desktop_app.MainWindow()
    window.switch_page(1)
    assert window.nav_names[1] == 'AI 任务'
    assert not window.send_job.isEnabled()
    assert not window.stop_job.isEnabled()
    root = tmp_path / 'workspace' / '测试'; (root / 'outputs').mkdir(parents=True)
    manager.jobs['test'] = {'task_id': 'test', 'title': '测试', 'workspace': str(root), 'state': 'running',
                            'entries': [{'role': 'tool', 'text': 'read_file · source/题目.pdf'}], 'pending_prompts': []}
    window.selected_job = 'test'; window.render_ai(force=True)
    assert window.stop_job.isEnabled() and window.send_job.text() == '加入队列 ↗'
    assert 'read_file' in window.job_output.toPlainText()
    assert not window.job_progress.isHidden()
    manager.jobs['test']['state'] = 'stopping'; window.render_ai(force=True)
    assert not window.stop_job.isEnabled() and not window.send_job.isEnabled()
    manager.jobs['test']['state'] = 'completed'; window.render_ai(force=True)
    assert window.reconnect_job_button.isEnabled() and window.job_progress.isHidden()

    errors = []
    def save_dialog():
        try:
            dialog = app.activeModalWidget()
            assert isinstance(dialog, QDialog)
            provider = dialog.findChild(QComboBox, 'modelProvider')
            url = dialog.findChild(QLineEdit, 'modelBaseUrl')
            model = dialog.findChild(QComboBox, 'modelName')
            key = dialog.findChild(QLineEdit, 'modelApiKey')
            timeout = dialog.findChild(QLineEdit, 'modelTimeout')
            rounds = dialog.findChild(QLineEdit, 'modelRounds')
            assert timeout.text() == rounds.text() == '0'
            assert '无限制' in timeout.placeholderText()
            from ai_presets import PRESETS
            url.setText('http://localhost:1234/v1'); model.setCurrentText('custom-model'); key.setText('test-ui-key')
            for name in ('deepseek', 'openai', 'anthropic', 'kimi', 'kimi_global', 'gemini', 'grok'):
                provider.setCurrentIndex(provider.findData(name))
                assert url.text() == PRESETS[name]['base_url']
                assert model.currentText() == PRESETS[name]['models'][0]
                assert key.text() == ''
                assert timeout.text() == rounds.text() == '0'
            provider.setCurrentIndex(provider.findData('custom'))
            assert model.currentText() == 'custom-model' and key.text() == 'test-ui-key'
            assert url.text() == 'http://localhost:1234/v1'
            next(b for b in dialog.findChildren(QPushButton) if b.text() == '保存配置').click()
        except BaseException as error:
            errors.append(error)
            if app.activeModalWidget(): app.activeModalWidget().reject()
    QTimer.singleShot(0, save_dialog)
    window.configure_model()
    assert not errors
    from ai_models import configuration
    assert configuration()['model'] == 'custom-model'
    assert configuration()['timeout'] == configuration()['max_rounds'] == 0
    assert b'test-ui-key' not in (tmp_path / 'ai-model.dpapi').read_bytes()
    window.close()


def test_closing_model_dialog_cancels_unlimited_connection_test(tmp_path, monkeypatch):
    import threading
    import time
    import ai_models
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    ai_models.save_configuration({'base_url': 'http://localhost:1234/v1', 'model': 'local',
                                  'timeout': 0, 'max_rounds': 0})
    monkeypatch.setattr(desktop_app.backend, 'monitor', lambda: None)
    monkeypatch.setattr(desktop_app.MainWindow, 'check_updates', lambda self: None)
    monkeypatch.setattr(desktop_app, 'bridge', ai_tasks.TaskManager())
    entered, stopped = threading.Event(), threading.Event()
    def complete(self, messages, schemas, cancel):
        entered.set()
        if cancel.wait(3):
            stopped.set()
            raise ai_models.Cancelled()
        raise AssertionError('连接测试没有取消')
    monkeypatch.setattr(ai_models.ModelClient, 'complete', complete)
    app = QApplication.instance() or QApplication([])
    window = desktop_app.MainWindow()
    errors = []
    def start_and_close():
        dialog = app.activeModalWidget()
        try:
            next(b for b in dialog.findChildren(QPushButton) if b.text() == '测试连接与工具调用').click()
            assert entered.wait(2)
        except BaseException as error:
            errors.append(error)
        finally:
            dialog.reject()
    QTimer.singleShot(0, start_and_close)
    window.configure_model()
    assert stopped.wait(2)
    deadline = time.monotonic() + 2
    while window.active_workers and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert not errors and not window.active_workers and not window._model_test_cancels
    window.close()
