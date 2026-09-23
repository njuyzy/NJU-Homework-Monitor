"""Offscreen smoke test for task controls and the model configuration dialog."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLineEdit, QPushButton

import storage
import ai_tasks
import desktop_app


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
            from ai_presets import PRESETS
            url.setText('http://localhost:1234/v1'); model.setCurrentText('custom-model'); key.setText('test-ui-key')
            for name in ('deepseek', 'openai', 'anthropic', 'kimi', 'kimi_global', 'gemini', 'grok'):
                provider.setCurrentIndex(provider.findData(name))
                assert url.text() == PRESETS[name]['base_url']
                assert model.currentText() == PRESETS[name]['models'][0]
                assert key.text() == ''
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
    assert b'test-ui-key' not in (tmp_path / 'ai-model.dpapi').read_bytes()
    window.close()
