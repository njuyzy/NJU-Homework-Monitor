"""Native rich-text transcript with Markdown messages and compact tool activity."""
from pathlib import Path
import re

from PySide6.QtCore import QByteArray, QUrl
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QFontDatabase,
                          QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument,
                          QTextDocumentFragment, QTextFormat, QTextFrameFormat, QTextLength, QTextTable)
from PySide6.QtWidgets import QTextBrowser


class TranscriptDocument(QTextDocument):
    def loadResource(self, resource_type, name):
        # Messages can link to files, but must not automatically load local or
        # remote images when their Markdown is displayed.
        return QByteArray()


class ChatView(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        document = TranscriptDocument(self)
        document.setDefaultFont(QFont('Microsoft YaHei UI', 11))
        document.setDocumentMargin(18)
        self.setDocument(document)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._open_link)
        self._key = None
        self._task_id = None
        self._entries = []
        self._activity = ''
        self._workspace = None
        self._expanded = set()
        self._fragments = {}

    def _markdown(self, text):
        fragment = self._fragments.get(text)
        if fragment is not None:
            return fragment
        doc = TranscriptDocument()
        doc.setDefaultFont(self.document().defaultFont())
        doc.setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub |
                        QTextDocument.MarkdownFeature.MarkdownNoHTML)
        code_ranges = []
        code_start = code_end = None
        block = doc.begin()
        while block.isValid():
            cursor = QTextCursor(block)
            style = block.blockFormat()
            style.setLineHeight(145, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            style.setTopMargin(4)
            style.setBottomMargin(9)
            heading = style.headingLevel()
            if heading:
                style.setTopMargin(18)
                style.setBottomMargin(10)
                cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                chars = QTextCharFormat()
                chars.setFontPointSize({1: 20, 2: 17, 3: 14}.get(heading, 12))
                chars.setFontWeight(QFont.Weight.Bold)
                chars.setForeground(QColor('#342b40'))
                cursor.mergeCharFormat(chars)
            if style.hasProperty(QTextFormat.Property.BlockCodeFence):
                if code_start is None:
                    code_start = block.position()
                code_end = block.position() + block.length() - 1
                style.setTopMargin(0)
                style.setBottomMargin(0)
                style.setLeftMargin(0)
                style.setRightMargin(0)
                style.setLineHeight(135, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
                cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                chars = QTextCharFormat()
                chars.setFontFamilies([QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()])
                chars.setFontPointSize(10)
                cursor.mergeCharFormat(chars)
            elif code_start is not None:
                code_ranges.append((code_start, code_end))
                code_start = code_end = None
            if style.property(QTextFormat.Property.BlockQuoteLevel):
                style.setBackground(QColor('#f8f6fa'))
                style.setLeftMargin(18)
                style.setRightMargin(8)
            # Use an unselected cursor: a paragraph selection can include the
            # next block and accidentally style its heading as code as well.
            QTextCursor(block).setBlockFormat(style)
            block = block.next()
        if code_start is not None:
            code_ranges.append((code_start, code_end))
        for frame in doc.rootFrame().childFrames():
            if isinstance(frame, QTextTable):
                style = frame.format()
                style.setBorder(1)
                style.setBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
                style.setBorderBrush(QColor('#e2dce9'))
                style.setCellPadding(8)
                style.setCellSpacing(0)
                style.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
                frame.setFormat(style)
                for column in range(frame.columns()):
                    cell = frame.cellAt(0, column)
                    cell_style = cell.format()
                    cell_style.setBackground(QColor('#f3f0f7'))
                    cell.setFormat(cell_style)
        for start, end in reversed(code_ranges):
            cursor = QTextCursor(doc)
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            style = QTextFrameFormat()
            style.setBackground(QColor('#f3f1f6'))
            style.setPadding(12)
            style.setTopMargin(8)
            style.setBottomMargin(12)
            style.setBorder(0)
            cursor.insertFrame(style)
        fragment = (QTextDocumentFragment(doc), QTextBlockFormat(doc.begin().blockFormat()))
        # Keep finished messages cached; only the streaming message changes.
        if len(self._fragments) >= 200:
            self._fragments.clear()
        self._fragments[text] = fragment
        return fragment

    def _section(self, label, color, background, text='', markdown=True, anchor=None):
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        frame_style = QTextFrameFormat()
        frame_style.setBorder(0)
        frame_style.setPadding(12)
        frame_style.setTopMargin(4)
        frame_style.setBottomMargin(12)
        frame_style.setBackground(QBrush(QColor(background)))
        frame = cursor.insertFrame(frame_style)
        cursor = frame.firstCursorPosition()
        label_style = QTextCharFormat()
        label_style.setForeground(QColor(color))
        label_style.setFontPointSize(10)
        label_style.setFontWeight(QFont.Weight.DemiBold)
        if anchor:
            label_style.setAnchor(True)
            label_style.setAnchorHref(anchor)
        cursor.insertText(label, label_style)
        cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
        if markdown:
            fragment, first_block = self._markdown(text)
            cursor.setBlockFormat(first_block)
            cursor.insertFragment(fragment)
        else:
            style = QTextCharFormat()
            style.setFontPointSize(10)
            style.setForeground(QColor('#77717f'))
            cursor.insertText(text, style)

    def show_conversation(self, task_id, entries, activity='', workspace=None, force=False):
        entries = [(entry.get('role', 'assistant'), entry.get('text', '')) for entry in entries[-200:]]
        changed_task = task_id != self._task_id
        if changed_task:
            self._expanded.clear()
            self._fragments.clear()
        key = (task_id, tuple(entries), activity, tuple(sorted(self._expanded)))
        self._workspace = Path(workspace).resolve() if workspace else None
        if not force and key == self._key:
            return
        bar = self.verticalScrollBar()
        selection = self.textCursor()
        follow = changed_task or (not selection.hasSelection() and bar.maximum() - bar.value() < 30)
        old_scroll = bar.value()
        selected = (selection.anchor(), selection.position()) if selection.hasSelection() and not changed_task else None
        self._key, self._task_id = key, task_id
        self._entries, self._activity = entries, activity
        self.document().clear()
        index = 0
        while index < len(entries):
            role, text = entries[index]
            if role == 'tool':
                end = index + 1
                while end < len(entries) and entries[end][0] == 'tool':
                    end += 1
                expanded = index in self._expanded
                tools = [value for _, value in entries[index:end]]
                detail = '\n'.join(tools) if expanded else tools[-1]
                self._section(f'{"收起" if expanded else "展开"}工具过程 · {len(tools)} 项',
                              '#8b8096', '#f7f7f9', detail, markdown=False, anchor=f'task-tools:{index}')
                index = end
                continue
            if text.strip():
                self._section('你' if role == 'user' else 'AI 助手',
                              '#7956a2' if role == 'user' else '#449078',
                              '#f6f1fa' if role == 'user' else '#ffffff', text)
            index += 1
        if activity:
            self._section('正在处理', '#a87830', '#fff9ed', activity, markdown=False)
        if not entries and not activity:
            self._section('AI 对话', '#7956a2', '#faf8fc',
                          '### 回复会显示在这里\n\n选择任务后，可查看处理过程并继续补充要求。')
        if selected:
            cursor = self.textCursor()
            limit = self.document().characterCount() - 1
            cursor.setPosition(min(selected[0], limit))
            cursor.setPosition(min(selected[1], limit), QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(cursor)
        bar.setValue(bar.maximum() if follow else old_scroll)

    def _open_link(self, url):
        if url.scheme() == 'task-tools':
            try:
                group = int(url.path())
            except ValueError:
                return
            self._expanded.symmetric_difference_update({group})
            entries = [{'role': role, 'text': text} for role, text in self._entries]
            self.show_conversation(self._task_id, entries, self._activity, self._workspace, force=True)
        elif url.scheme().lower() in ('http', 'https'):
            QDesktopServices.openUrl(url)
        elif self._workspace:
            value = url.toLocalFile() if url.isLocalFile() else url.toString()
            if not url.isLocalFile() and url.scheme() and not re.match(r'^[A-Za-z]:[/\\]', value):
                return
            path = (self._workspace / value).resolve()
            if path.is_relative_to(self._workspace) and path.is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
