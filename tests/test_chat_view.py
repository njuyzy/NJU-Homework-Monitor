import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor, QTextTable
from PySide6.QtWidgets import QApplication

from chat_view import ChatView


@pytest.fixture
def view():
    app = QApplication.instance() or QApplication([])
    widget = ChatView()
    widget.resize(600, 350)
    widget.show()
    app.processEvents()
    yield widget
    widget.close()


def test_markdown_has_real_heading_list_code_and_table(view):
    message = ('# 实验结果\n\n这段是 **重点**。\n\n'
               '- 检查通过\n- 文件已保存\n\n> 引用说明\n\n'
               '```python\nprint("hello")\n```\n\n'
               '| 文件 | 状态 |\n| --- | --- |\n| 报告 | 完成 |')
    view.show_conversation('first', [{'role': 'user', 'text': '请整理结果'},
                                     {'role': 'assistant', 'text': message}])
    text = view.toPlainText()
    assert '实验结果' in text and '**重点**' not in text and '```' not in text
    assert '你' in text and 'AI 助手' in text
    blocks = []
    block = view.document().begin()
    while block.isValid():
        blocks.append(block)
        block = block.next()
    assert any(block.blockFormat().headingLevel() == 1 for block in blocks)
    assert any(block.textList() for block in blocks)
    assert '<pre' in view.toHtml()
    assert any(isinstance(child, QTextTable) for frame in view.document().rootFrame().childFrames()
               for child in frame.childFrames())


def test_tool_activity_collapses_and_resets_between_tasks(view):
    entries = [{'role': 'tool', 'text': 'read_file · first.txt'},
               {'role': 'tool', 'text': 'run_code · check.py'},
               {'role': 'assistant', 'text': '## 已完成'}]
    view.show_conversation('first', entries)
    assert '工具过程 · 2 项' in view.toPlainText()
    assert 'first.txt' not in view.toPlainText() and 'check.py' in view.toPlainText()
    view._open_link(QUrl('task-tools:0'))
    assert 'first.txt' in view.toPlainText()
    view.show_conversation('second', [{'role': 'assistant', 'text': '另一个任务'}])
    assert 'first.txt' not in view.toPlainText() and '已完成' not in view.toPlainText()


def test_updates_preserve_reading_position_and_selection(view):
    entries = [{'role': 'assistant', 'text': '\n\n'.join(f'段落 {i}' for i in range(100))}]
    view.show_conversation('first', entries)
    QApplication.processEvents()
    view.verticalScrollBar().setValue(0)
    cursor = view.document().find('段落 2')
    view.setTextCursor(cursor)
    old_scroll = view.verticalScrollBar().value()
    entries.append({'role': 'assistant', 'text': '新内容'})
    view.show_conversation('first', entries, '正在生成回复')
    assert view.textCursor().selectedText() == '段落 2'
    assert view.verticalScrollBar().value() == old_scroll
    revision = view.document().revision()
    view.show_conversation('first', entries, '正在生成回复')
    assert view.document().revision() == revision


def test_links_only_open_web_or_current_workspace_files(view, tmp_path, monkeypatch):
    workspace = tmp_path / 'task'
    workspace.mkdir()
    answer = workspace / 'answer.md'
    answer.write_text('answer', encoding='utf-8')
    outside = tmp_path / 'private.txt'
    outside.write_text('private', encoding='utf-8')
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url) or True)
    view.show_conversation('first', [{'role': 'assistant', 'text': '[答案](answer.md)'}], workspace=workspace)
    view._open_link(QUrl('https://example.com'))
    view._open_link(QUrl('answer.md'))
    view._open_link(QUrl.fromLocalFile(str(outside)))
    view._open_link(QUrl('javascript:alert(1)'))
    assert opened == [QUrl('https://example.com'), QUrl.fromLocalFile(str(answer))]
    assert view.document().loadResource(2, QUrl.fromLocalFile(str(outside))).isEmpty()
