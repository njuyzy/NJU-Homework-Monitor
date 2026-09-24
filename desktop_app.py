"""Native PySide6 desktop client for NJU Homework Monitor."""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (QEasingCurve, QObject, Property, QPropertyAnimation, QRunnable,
                            QRectF, QSize, Qt, QThreadPool, QTimer, QUrl, Signal)
from PySide6.QtGui import QColor, QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QStackedWidget, QTextBrowser, QTextEdit, QVBoxLayout,
    QWidget,
)

import app as backend
import storage
import updates
from task_status import deadline, display_status, matches_filter
from ai_tasks import manager as bridge
from ai_models import ModelClient, configuration, model_profiles, save_configuration
from ai_presets import PRESETS, preset_configuration
from windows_integration import acquire_instance_lock, autostart, capabilities, release_instance_lock


PURPLE = '#7956a2'
PURPLE_DARK = '#68478b'
GREEN = '#449078'
AMBER = '#b98530'
ROSE = '#c26b77'
INK = '#252833'
MUTED = '#8b8d9a'
LINE = '#ececf1'
_resource_root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
EMBLEM_PATH = _resource_root / 'static' / 'nju-emblem-color.png'
STATUS_TEXT = {'pending': '未提交', 'draft': '草稿', 'submitted': '已提交', 'unknown': '待核实', 'overdue': '已逾期'}
STATUS_BADGE = {'pending': (AMBER, '#f7ecd9'), 'draft': (AMBER, '#f7ecd9'),
                'submitted': (GREEN, '#e8f3ef'), 'unknown': (MUTED, '#f0eef2'), 'overdue': (ROSE, '#fbecef')}
JOB_TEXT = {'starting': '正在启动', 'running': '处理中', 'completed': '已完成',
            'failed': '失败', 'error': '需要处理', 'interrupted': '已暂停', 'stopping': '正在停止'}


def format_date(value):
    if not value:
        return '未设置截止时间'
    try:
        return datetime.fromisoformat(value).astimezone().strftime('%m月%d日 %H:%M')
    except (ValueError, TypeError):
        return str(value)


class WorkerSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Worker(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.done.emit(self.function())
        except Exception as error:
            self.signals.failed.emit(str(error) or type(error).__name__)


def status_badge(text, status):
    fg, bg = STATUS_BADGE.get(status, (MUTED, '#f0eef2'))
    badge = QLabel(text)
    badge.setStyleSheet(f'border-radius:6px; padding:3px 9px; font-size:11px; color:{fg}; background:{bg};')
    return badge


class ToggleSwitch(QAbstractButton):
    """Animated switch matching the original web UI."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(38, 22)
        self._knob_x = 3.0
        self._animation = QPropertyAnimation(self, b'knobPosition', self)
        self._animation.setDuration(170)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self):
        return QSize(38, 22)

    def _get_knob_position(self):
        return self._knob_x

    def _set_knob_position(self, value):
        self._knob_x = float(value)
        self.update()

    knobPosition = Property(float, _get_knob_position, _set_knob_position)

    def _animate(self, checked):
        self._animation.stop()
        self._animation.setStartValue(self._knob_x)
        self._animation.setEndValue(21.0 if checked else 3.0)
        self._animation.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            track = QColor('#ece8ef')
        else:
            track = QColor('#9672b0' if self.isChecked() else '#e2dde7')
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(1, 1, 36, 20), 10, 10)
        painter.setBrush(QColor('#ffffff'))
        painter.drawEllipse(QRectF(self._knob_x, 4, 14, 14))


def setting_switch_row(title, description, toggle):
    row = QFrame(); row.setObjectName('settingRow')
    layout = QHBoxLayout(row); layout.setContentsMargins(0, 13, 0, 13)
    copy = QVBoxLayout(); copy.setSpacing(5)
    heading = QLabel(title); heading.setObjectName('settingTitle')
    note = QLabel(description); note.setObjectName('settingNote'); note.setWordWrap(True)
    copy.addWidget(heading); copy.addWidget(note)
    layout.addLayout(copy, 1); layout.addWidget(toggle, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


class TaskCard(QFrame):
    details = Signal(str)
    launch_ai = Signal(str)

    def __init__(self, task, job=None):
        super().__init__()
        self.setObjectName('card')
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 15, 18, 15)
        row.setSpacing(14)
        symbol = QLabel('?' if task.get('kind') == 'quiz' else '▤')
        symbol.setObjectName('taskSymbol')
        symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        symbol.setFixedSize(34, 34)
        text = QVBoxLayout()
        text.setSpacing(4)
        course = QLabel(task.get('course', '课程'))
        course.setObjectName('eyebrow')
        title = QPushButton(task.get('title', '未命名作业'))
        title.setObjectName('linkButton')
        title.clicked.connect(lambda: self.details.emit(task['id']))
        meta = QHBoxLayout()
        meta.setSpacing(8)
        status = display_status(task)
        meta.addWidget(status_badge(STATUS_TEXT.get(status, '待核实'), status))
        if status == 'overdue' and task.get('status') == 'draft':
            meta.addWidget(status_badge('草稿', 'draft'))
        due = QLabel(f'◷ {format_date(task.get("due"))}')
        due.setObjectName('muted')
        meta.addWidget(due)
        if task.get('stale'):
            meta.addWidget(status_badge('旧数据待更新', 'unknown'))
        if task.get('warning'):
            meta.addWidget(status_badge('截止时间待核实', 'pending'))
        meta.addStretch()
        text.addWidget(course)
        text.addWidget(title)
        text.addLayout(meta)
        actions = QVBoxLayout()
        actions.setSpacing(8)
        ai = QPushButton('✦ AI 处理中' if job and job.get('state') in ('starting', 'running') else '✦ AI 一键完成')
        ai.setObjectName('softButton')
        ai.setEnabled(not job or job.get('state') not in ('starting', 'running'))
        ai.clicked.connect(lambda: self.launch_ai.emit(task['id']))
        source = QPushButton('打开作业 ↗')
        source.setObjectName('linkButtonSmall')
        source.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(task.get('url', ''))))
        actions.addWidget(ai)
        actions.addWidget(source)
        row.addWidget(symbol, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(text, 1)
        row.addLayout(actions)


class CourseCard(QFrame):
    toggled = Signal(str, bool)

    def __init__(self, course, pending_count):
        super().__init__()
        self.setObjectName('card')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 17, 18, 17)
        layout.setSpacing(6)
        badge = status_badge('当前课程' if course.get('current') else '非当前学期',
                             'pending' if course.get('current') else 'unknown')
        title = QPushButton(course.get('name', '未命名课程') + ' ↗')
        title.setObjectName('linkButton')
        title.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(course.get('url', ''))))
        count = QLabel(f'{pending_count} 项待完成作业')
        count.setObjectName('muted')
        toggle = ToggleSwitch()
        toggle.setChecked(bool(course.get('monitored')))
        toggle.toggled.connect(lambda checked: self.toggled.emit(course['id'], checked))
        toggle_row = QHBoxLayout()
        toggle_label = QLabel('监控这门课'); toggle_label.setObjectName('settingTitle')
        toggle_row.addWidget(toggle_label); toggle_row.addStretch(); toggle_row.addWidget(toggle)
        layout.addWidget(badge, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(title)
        layout.addWidget(count)
        layout.addSpacing(8)
        layout.addLayout(toggle_row)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('课后 · 南大作业提醒')
        self.resize(1280, 800)
        self.setMinimumSize(920, 620)
        self.thread_pool = QThreadPool.globalInstance()
        self.snapshot = {}
        self.settings = {}
        self.selected_job = None
        self.pending_attachments = []
        self.signatures = {}
        self.nav_buttons = []
        self.active_workers = set()
        self.update_info = None
        self.checking_updates = False
        self.nav_names = ('作业总览', 'AI 任务', '我的课程', '提醒设置')
        self.page_animation = None
        self.build_ui()
        self.apply_style()
        backend.stop.clear()
        threading.Thread(target=backend.monitor, daemon=True).start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_ui)
        self.timer.start(2000)
        self.ai_timer = QTimer(self)
        self.ai_timer.timeout.connect(self.render_ai)
        self.ai_timer.start(350)
        self.refresh_ui(force=True)
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.check_updates)
        self.update_timer.start(15 * 60 * 1000)
        QTimer.singleShot(0, self.check_updates)

    def build_ui(self):
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(220)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(22, 28, 22, 24)
        brand_row = QHBoxLayout()
        brand_mark = QLabel('课'); brand_mark.setObjectName('brandMark')
        brand_mark.setFixedSize(44, 44); brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_text = QVBoxLayout(); brand_text.setSpacing(1)
        brand = QLabel('课后'); brand.setObjectName('brandTitle')
        subtitle = QLabel('NJU · HOMEWORK'); subtitle.setObjectName('brandSub')
        brand_text.addWidget(brand); brand_text.addWidget(subtitle)
        brand_row.addWidget(brand_mark); brand_row.addSpacing(4); brand_row.addLayout(brand_text); brand_row.addStretch()
        term_month = datetime.now().month
        term_label = QLabel('我的学习空间'); term_label.setObjectName('tinyLabel')
        term_title = QLabel(f'{datetime.now().year} · {"秋季" if term_month >= 8 or term_month == 1 else "春季"}学期'); term_title.setObjectName('termTitle')
        term_sub = QLabel('把截止时间，交给课后。'); term_sub.setObjectName('muted')
        side.addLayout(brand_row)
        side.addSpacing(30)
        side.addWidget(term_label)
        side.addWidget(term_title)
        side.addWidget(term_sub)
        side.addSpacing(26)
        self.pages = QStackedWidget()
        for index, (icon, name) in enumerate(zip(('▦', '✦', '▤', '⚙'), self.nav_names)):
            button = QPushButton(f'{icon}   {name}')
            button.setObjectName('navButton')
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self.switch_page(i))
            self.nav_buttons.append(button)
            side.addWidget(button)
        side.addStretch()
        self.connection = QLabel('●  等待连接')
        self.connection.setObjectName('muted')
        side.addWidget(self.connection)
        local_note = QLabel('数据保存在这台电脑\n北京时间 · UTC+8'); local_note.setObjectName('sidebarNote')
        side.addWidget(local_note)
        self.build_overview()
        self.build_ai()
        self.build_courses()
        self.build_settings()
        outer.addWidget(sidebar)
        outer.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self.statusBar().setSizeGripEnabled(False)
        self.switch_page(0)

    def page_shell(self, title, subtitle, action=None, eyebrow=None):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(36, 0, 36, 26)
        topbar = QHBoxLayout()
        crumb = QLabel(f'我的学习空间   /   <b>{title.replace("。", "")}</b>')
        crumb.setObjectName('breadcrumb')
        today = QLabel(datetime.now().strftime('%Y年%m月%d日'))
        today.setObjectName('muted')
        topbar.addWidget(crumb); topbar.addStretch(); topbar.addWidget(today)
        topbar_widget = QWidget(); topbar_widget.setObjectName('topbar'); topbar_widget.setLayout(topbar)
        layout.addWidget(topbar_widget)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(4)
        if eyebrow:
            eye = QLabel(eyebrow)
            eye.setObjectName('eyebrow')
            heading.addWidget(eye)
        label = QLabel(title)
        label.setObjectName('pageTitle')
        note = QLabel(subtitle)
        note.setObjectName('muted')
        heading.addWidget(label)
        heading.addWidget(note)
        header.addLayout(heading, 1)
        if action:
            header.addWidget(action)
        layout.addLayout(header)
        layout.addSpacing(14)
        return page, layout

    def build_overview(self):
        self.sync_button = QPushButton('↻ 立即同步')
        self.sync_button.setObjectName('primaryButton')
        self.sync_button.clicked.connect(lambda: self.run_async(backend.sync, '同步完成'))
        self.update_button = QPushButton('发现更新')
        self.update_button.setObjectName('softButton')
        self.update_button.hide()
        self.update_button.clicked.connect(self.open_update)
        actions = QWidget(); action_row = QHBoxLayout(actions)
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.addWidget(self.update_button); action_row.addWidget(self.sync_button)
        page, layout = self.page_shell('每份作业，都有着落。', '未完成的任务和截止时间，一眼看清。', actions, eyebrow='LESS RUSH. MORE FOCUS.')
        self.status_label = QLabel('正在读取本机状态…')
        self.status_label.setObjectName('statusBanner')
        layout.addWidget(self.status_label)
        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.stat_labels = {}
        self.stat_buttons = {}
        for key, title, sub, color in (
            ('pending', '待完成', '未交、草稿与待核实', PURPLE),
            ('soon', '72 小时内截止', '给重要的事留一点余量', AMBER),
            ('overdue', '已逾期', '查看原网页能否补交', ROSE),
            ('submitted', '已提交', '每一次完成，都算数', GREEN),
        ):
            card = QPushButton(); card.setObjectName('statCard')
            card.setMinimumHeight(116)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            card.setCheckable(True)
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setAccessibleName(title)
            card.clicked.connect(lambda checked=False, mode=key: self.select_task_filter(mode))
            box = QVBoxLayout(card)
            box.setContentsMargins(16, 14, 16, 14)
            box.setSpacing(4)
            lab = QLabel(title); lab.setObjectName('statLabel')
            value = QLabel('—'); value.setObjectName('statValue')
            value.setStyleSheet(f'color: {color};')
            note = QLabel(sub); note.setObjectName('statSub')
            box.addWidget(lab)
            box.addWidget(value)
            box.addWidget(note)
            for label in (lab, value, note):
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.stat_labels[key] = value
            self.stat_buttons[key] = card
            stats.addWidget(card)
        layout.addLayout(stats)
        workspace = QHBoxLayout()
        workspace.setSpacing(20)
        list_panel = QFrame(); list_panel.setObjectName('panel')
        list_box = QVBoxLayout(list_panel); list_box.setContentsMargins(0, 0, 0, 0); list_box.setSpacing(0)
        tools = QHBoxLayout()
        tools.setContentsMargins(20, 16, 20, 12)
        list_title = QLabel('作业清单'); list_title.setObjectName('sectionTitle')
        tools.addWidget(list_title)
        self.task_filter = QComboBox()
        self.task_filter.addItem('待完成', 'pending')
        self.task_filter.addItem('全部', 'all')
        self.task_filter.addItem('已提交', 'submitted')
        self.task_filter.addItem('已逾期', 'overdue')
        self.task_filter.addItem('72 小时内截止', 'soon')
        self.task_filter.addItem('草稿', 'draft')
        self.task_filter.addItem('待核实', 'unknown')
        self.task_filter.currentIndexChanged.connect(lambda: self.render_tasks(force=True))
        self.task_search = QLineEdit(); self.task_search.setPlaceholderText('搜索作业…')
        self.task_search.setMaximumWidth(150); self.task_search.textChanged.connect(lambda: self.render_tasks(force=True))
        self.course_filter = QComboBox(); self.course_filter.addItem('全部课程', 'all')
        self.course_filter.setMaximumWidth(170); self.course_filter.currentIndexChanged.connect(lambda: self.render_tasks(force=True))
        tools.addStretch(); tools.addWidget(self.task_filter); tools.addWidget(self.task_search); tools.addWidget(self.course_filter)
        list_box.addLayout(tools)
        self.task_container = QWidget()
        self.task_layout = QVBoxLayout(self.task_container)
        self.task_layout.setContentsMargins(0, 0, 0, 0)
        self.task_layout.setSpacing(9)
        self.task_scroll = QScrollArea(); self.task_scroll.setWidgetResizable(True); self.task_scroll.setWidget(self.task_container)
        self.task_scroll.setFrameShape(QFrame.Shape.NoFrame)
        list_box.addWidget(self.task_scroll, 1)
        workspace.addWidget(list_panel, 1)

        focus = QVBoxLayout(); focus.setSpacing(13)
        deadline_card = QFrame(); deadline_card.setObjectName('focusCard'); deadline_card.setFixedWidth(236)
        deadline_box = QVBoxLayout(deadline_card); deadline_box.setContentsMargins(20, 20, 20, 20)
        deadline_label = QLabel('◷  下一个截止时间'); deadline_label.setObjectName('focusLabel')
        self.next_deadline = QLabel('等待首次同步\n\n连接学校账号后，这里会显示最需要关注的作业。')
        self.next_deadline.setObjectName('focusText'); self.next_deadline.setWordWrap(True)
        deadline_box.addWidget(deadline_label); deadline_box.addSpacing(8); deadline_box.addWidget(self.next_deadline); deadline_box.addStretch()
        tip = QFrame(); tip.setObjectName('sideCard'); tip_box = QVBoxLayout(tip)
        tip_title = QLabel('✧  让 AI 帮你开个头'); tip_title.setObjectName('sectionTitle')
        tip_text = QLabel('点击作业旁的「AI 一键完成」，将题目交给 AI 处理。\n\n任务完成后，由你决定提交。')
        tip_text.setObjectName('muted'); tip_text.setWordWrap(True)
        tip_box.addWidget(tip_title); tip_box.addWidget(tip_text)
        self.sync_info = QLabel('●  后台监控\n尚未同步'); self.sync_info.setObjectName('syncInfo')
        focus.addWidget(deadline_card); focus.addWidget(tip); focus.addWidget(self.sync_info); focus.addStretch()
        workspace.addLayout(focus)
        layout.addLayout(workspace, 1)
        self.pages.addWidget(page)

    def build_ai(self):
        ai_actions = QWidget(); ai_action_row = QHBoxLayout(ai_actions)
        ai_action_row.setContentsMargins(0, 0, 0, 0); ai_action_row.setSpacing(8)
        refresh_output = QPushButton('↻ 刷新输出'); refresh_output.clicked.connect(lambda: self.render_ai(force=True))
        self.more_button = QPushButton('更多接口 ···')
        more_menu = QMenu(self.more_button)
        self.model_settings_action = more_menu.addAction('⚙  自定义模型设置')
        self.menu_open_workspace = more_menu.addAction('▣  打开工作目录')
        self.menu_copy_id = more_menu.addAction('⌁  复制任务 ID')
        self.model_settings_action.triggered.connect(self.configure_model)
        self.menu_open_workspace.triggered.connect(self.open_job_workspace)
        self.menu_copy_id.triggered.connect(self.copy_job_id)
        self.more_button.setMenu(more_menu)
        ai_action_row.addWidget(refresh_output); ai_action_row.addWidget(self.more_button)
        page, layout = self.page_shell('AI 任务', '查看处理过程、继续补充要求，并接收 AI 的实时输出。', ai_actions, eyebrow='AI WORKSPACE')
        splitter = QSplitter()
        self.job_list = QListWidget()
        self.job_list.setMinimumWidth(205)
        self.job_list.currentItemChanged.connect(self.select_job)
        center = QFrame(); center.setObjectName('panel')
        center_layout = QVBoxLayout(center)
        self.job_title = QLabel('选择一个 AI 任务'); self.job_title.setObjectName('sectionTitle')
        self.job_state = QLabel('等待任务'); self.job_state.setObjectName('badge')
        top = QHBoxLayout(); top.addWidget(self.job_title, 1); top.addWidget(self.job_state)
        self.job_progress = QProgressBar(); self.job_progress.setRange(0, 0)
        self.job_progress.setTextVisible(False); self.job_progress.setFixedHeight(3); self.job_progress.hide()
        self.job_progress.setStyleSheet('QProgressBar { border: none; background: #f2edf8; } QProgressBar::chunk { background: #a98bc0; }')
        self.job_output = QTextBrowser(); self.job_output.setOpenExternalLinks(True)
        self.job_input = QTextEdit(); self.job_input.setPlaceholderText('给 AI 补充要求…'); self.job_input.setMaximumHeight(95)
        attach_row = QHBoxLayout()
        self.attach_button = QPushButton('📎 附件'); self.attach_button.clicked.connect(self.attach_files)
        self.attachment_label = QLabel('未附加文件'); self.attachment_label.setObjectName('muted'); self.attachment_label.setWordWrap(True)
        self.clear_attachments_button = QPushButton('清除'); self.clear_attachments_button.clicked.connect(self.clear_attachments)
        attach_row.addWidget(self.attach_button); attach_row.addWidget(self.attachment_label, 1); attach_row.addWidget(self.clear_attachments_button)
        actions = QHBoxLayout()
        self.stop_job = QPushButton('停止'); self.stop_job.clicked.connect(self.interrupt_job)
        self.reconnect_job_button = QPushButton('继续处理'); self.reconnect_job_button.clicked.connect(self.reconnect_job)
        self.send_job = QPushButton('发送 ↗'); self.send_job.setObjectName('primaryButton'); self.send_job.clicked.connect(self.continue_job)
        actions.addWidget(self.stop_job); actions.addWidget(self.reconnect_job_button); actions.addStretch(); actions.addWidget(self.send_job)
        center_layout.addLayout(top); center_layout.addWidget(self.job_progress); center_layout.addWidget(self.job_output, 1); center_layout.addWidget(self.job_input); center_layout.addLayout(attach_row); center_layout.addLayout(actions)
        context = QFrame(); context.setObjectName('infoPanel')
        context_layout = QVBoxLayout(context); context_layout.setContentsMargins(16, 16, 16, 16)
        context_title = QLabel('任务信息'); context_title.setObjectName('sectionTitle')
        self.job_info = QLabel('选择任务后查看课程、截止时间和当前活动。')
        self.job_info.setWordWrap(True); self.job_info.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.job_info.setMinimumWidth(185); self.job_info.setObjectName('contextText')
        files_title = QLabel('生成文件'); files_title.setObjectName('sectionTitle')
        self.job_files = QListWidget(); self.job_files.setObjectName('fileList')
        self.job_files.itemDoubleClicked.connect(self.open_generated_file)
        file_hint = QLabel('双击文件可打开'); file_hint.setObjectName('muted')
        context_layout.addWidget(context_title); context_layout.addWidget(self.job_info)
        context_layout.addSpacing(8); context_layout.addWidget(files_title); context_layout.addWidget(self.job_files, 1); context_layout.addWidget(file_hint)
        splitter.addWidget(self.job_list); splitter.addWidget(center); splitter.addWidget(context)
        splitter.setSizes([220, 650, 220])
        layout.addWidget(splitter, 1)
        self.pages.addWidget(page)

    def build_courses(self):
        page, layout = self.page_shell('我的课程', '当前学期自动监控，历史课程可以手动加入。', eyebrow='YOUR SEMESTER, TOGETHER.')
        self.course_container = QWidget()
        self.course_layout = QGridLayout(self.course_container)
        self.course_layout.setContentsMargins(0, 0, 0, 0)
        self.course_layout.setSpacing(10)
        self.course_scroll = QScrollArea(); self.course_scroll.setWidgetResizable(True); self.course_scroll.setWidget(self.course_container)
        self.course_scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.course_scroll, 1)
        self.pages.addWidget(page)

    def build_settings(self):
        page, layout = self.page_shell('提醒设置', '本机保存，按你的节奏运行。', eyebrow='MAKE IT YOURS.')
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(); form = QFormLayout(content)
        form.setContentsMargins(12, 8, 24, 20); form.setSpacing(18)
        self.monitoring = ToggleSwitch()
        self.notifications = ToggleSwitch()
        self.auto_login = ToggleSwitch()
        self.auto_start = ToggleSwitch()
        for name, widget in (('monitoring', self.monitoring), ('notifications', self.notifications), ('auto_login_window', self.auto_login)):
            widget.toggled.connect(lambda checked, key=name: self.save_setting(key, checked))
        self.auto_start.toggled.connect(self.save_autostart)
        self.interval = QComboBox()
        for minutes in (5, 15, 30, 60, 120, 240): self.interval.addItem(f'{minutes} 分钟' if minutes < 60 else f'{minutes // 60} 小时', minutes)
        self.interval.currentIndexChanged.connect(lambda: self.save_setting('interval_minutes', self.interval.currentData()))
        form.addRow(setting_switch_row('持续监控', '关闭窗口前持续检查作业与截止时间', self.monitoring))
        form.addRow(setting_switch_row('开机自动启动', '登录电脑时启动作业监控', self.auto_start))
        form.addRow(setting_switch_row('登录失效时自动打开浏览器', '需要认证时打开登录窗口，完成后恢复同步', self.auto_login))
        form.addRow(setting_switch_row('桌面通知', '作业到达提醒时间时发送系统通知', self.notifications))
        form.addRow('检查间隔', self.interval)
        model_button = QPushButton('配置自定义 AI 模型'); model_button.clicked.connect(self.configure_model)
        form.addRow('AI 任务', model_button)
        self.username = QLineEdit(); self.username.setPlaceholderText('学号')
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.EchoMode.Password); self.password.setPlaceholderText('密码')
        save_credential = QPushButton('加密保存凭据'); save_credential.clicked.connect(self.save_credentials)
        login = QPushButton('打开统一认证登录窗口 ↗'); login.setObjectName('primaryButton'); login.clicked.connect(lambda: self.run_async(lambda: backend.open_login_once(manual=True), '登录窗口已打开'))
        form.addRow('学号', self.username); form.addRow('密码', self.password); form.addRow(save_credential); form.addRow(login)
        scroll.setWidget(content); layout.addWidget(scroll, 1)
        self.pages.addWidget(page)

    def apply_style(self):
        self.setStyleSheet(f"""
            * {{ font-family: 'Segoe UI', 'Microsoft YaHei'; font-size: 14px; color: {INK}; }}
            QMainWindow, QWidget {{ background: #f7f8fa; }}
            #sidebar {{ background: white; border-right: 1px solid {LINE}; }}
            #brandMark {{ color: white; background: {PURPLE}; border-radius: 12px; font-size: 22px; font-weight: 600; }}
            #brandTitle {{ font-size: 22px; font-weight: 700; letter-spacing: 2px; }}
            #brandSub {{ color: #9a8aa9; font-size: 8px; letter-spacing: 1px; }}
            #sidebarNote {{ color: #aaa9b3; font-size: 10px; line-height: 1.8; }}
            #topbar {{ min-height: 64px; border-bottom: 1px solid {LINE}; background: transparent; }}
            #breadcrumb {{ color: #90909b; font-size: 11px; }}
            #eyebrow {{ color: #8d769d; font-size: 10px; }}
            #tinyLabel {{ color: {MUTED}; font-size: 10px; }}
            #termTitle {{ color: {INK}; font-size: 14px; font-weight: 600; }}
            #muted {{ color: {MUTED}; font-size: 12px; }}
            #pageTitle {{ font-size: 26px; font-weight: 700; color: {INK}; }}
            #sectionTitle {{ font-size: 15px; font-weight: 600; color: {INK}; }}
            QPushButton {{ border: 1px solid #e2dce7; border-radius: 8px; background: white; padding: 8px 14px; color: {INK}; }}
            QPushButton:hover {{ background: #f3eef7; border-color: #cdbbd9; }}
            QPushButton:pressed {{ background: #e9e0f0; }}
            QPushButton:disabled {{ color: #aaa4ad; background: #f4f3f5; }}
            #navButton {{ border: none; text-align: left; padding: 13px 14px; color: #81818e; border-radius: 9px; }}
            #navButton:hover {{ background: #f7f4fa; color: {INK}; }}
            #navButton:checked {{ color: {PURPLE}; background: #f2edf8; font-weight: 600; }}
            #primaryButton {{ color: white; background: {PURPLE}; border-color: {PURPLE}; font-weight: 600; }}
            #primaryButton:hover {{ background: {PURPLE_DARK}; border-color: {PURPLE_DARK}; }}
            #softButton {{ color: {PURPLE}; background: #f3eef7; border-color: #e9e0f0; }}
            #linkButton, #linkButtonSmall {{ border: none; padding: 2px 0; text-align: left; background: transparent; }}
            #linkButton {{ font-weight: 600; font-size: 14px; color: {INK}; }}
            #linkButton:hover, #linkButtonSmall:hover {{ color: {PURPLE}; }}
            #linkButtonSmall {{ color: #75657f; font-size: 11px; }}
            #statusBanner {{ padding: 14px 16px; background: #eff6f1; border: 1px solid #dfeae3; border-radius: 10px; color: #2f5a4a; }}
            #card, #statCard, #panel, #infoPanel {{ background: white; border: 1px solid #e8e6eb; border-radius: 12px; }}
            #focusCard {{ background: #f1ecf6; border: 1px solid #e8e0ef; border-radius: 12px; min-height: 190px; }}
            #sideCard {{ background: white; border: 1px solid #e8e6eb; border-radius: 11px; }}
            #focusLabel {{ color: #a18bb2; font-size: 11px; }}
            #focusText {{ color: #766185; font-size: 13px; line-height: 1.7; }}
            #syncInfo {{ color: #8f8995; font-size: 10px; line-height: 1.8; padding: 8px; }}
            #contextText {{ color: #6f6875; font-size: 11px; line-height: 1.7; }}
            #fileList {{ background: #fbfbfc; border: 1px solid #ece8ef; border-radius: 8px; }}
            #statCard {{ min-width: 118px; }}
            #statCard:hover {{ background: #faf7fd; border-color: #baa2d0; }}
            #statCard:checked {{ background: #f5effa; border: 2px solid {PURPLE}; }}
            #statLabel {{ color: {MUTED}; font-size: 12px; }}
            #statValue {{ font-size: 28px; font-weight: 700; }}
            #statSub {{ color: #9a91a0; font-size: 10px; }}
            #taskSymbol {{ background: #f2edf8; border-radius: 9px; color: {PURPLE}; font-size: 15px; }}
            #badge {{ color: {PURPLE}; background: #f2edf8; border-radius: 6px; padding: 5px 8px; font-size: 11px; }}
            #infoPanel {{ padding: 18px; line-height: 1.6; }}
            QScrollArea {{ background: transparent; border: none; }}
            QTextBrowser, QTextEdit, QLineEdit, QComboBox {{ background: white; border: 1px solid #e3dfe7; border-radius: 8px; padding: 7px; }}
            QTextBrowser:focus, QTextEdit:focus, QLineEdit:focus, QComboBox:focus {{ border: 1px solid #a98bc0; background: #fff; }}
            QListWidget {{ background: white; border: 1px solid #e8e6eb; border-radius: 12px; }}
            QListWidget::item {{ padding: 12px 9px; border-bottom: 1px solid #efedf1; }}
            QListWidget::item:hover {{ background: #f8f5fa; }}
            QListWidget::item:selected {{ color: {PURPLE}; background: #f2edf8; }}
            QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px 2px; }}
            QScrollBar::handle:vertical {{ background: #d8cfdf; min-height: 32px; border-radius: 4px; }}
            QScrollBar::handle:vertical:hover {{ background: #b9a5c8; }}
            QScrollBar::handle:vertical:pressed {{ background: {PURPLE}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; border: none; background: none; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
            QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 3px; }}
            QScrollBar::handle:horizontal {{ background: #d8cfdf; min-width: 32px; border-radius: 4px; }}
            QScrollBar::handle:horizontal:hover {{ background: #b9a5c8; }}
            QScrollBar::handle:horizontal:pressed {{ background: {PURPLE}; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; border: none; background: none; }}
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}
            QSplitter::handle {{ background: transparent; }}
            QSplitter::handle:horizontal {{ width: 8px; }}
            QSplitter::handle:horizontal:hover {{ background: #eee8f2; border-radius: 4px; }}
            QMenu {{ background: white; border: 1px solid #e6e1e9; border-radius: 8px; padding: 6px; }}
            QMenu::item {{ padding: 9px 24px 9px 12px; border-radius: 5px; }}
            QMenu::item:selected {{ color: {PURPLE}; background: #f2edf8; }}
            #settingRow {{ border-bottom: 1px solid #f1eef4; background: transparent; }}
            #settingTitle {{ color: {INK}; font-size: 12px; font-weight: 500; }}
            #settingNote {{ color: #aaa1b1; font-size: 10px; }}
            QComboBox::drop-down {{ border: none; width: 26px; }}
            QComboBox QAbstractItemView {{ background: white; border: 1px solid #e3dfe7; selection-background-color: #f2edf8; selection-color: {PURPLE}; }}
        """)

    def switch_page(self, index):
        self.pages.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons): button.setChecked(i == index)
        page = self.pages.currentWidget()
        effect = QGraphicsOpacityEffect(page); page.setGraphicsEffect(effect)
        self.page_animation = QPropertyAnimation(effect, b'opacity', self)
        self.page_animation.setDuration(180)
        self.page_animation.setStartValue(0.25); self.page_animation.setEndValue(1.0)
        self.page_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.page_animation.finished.connect(lambda: page.setGraphicsEffect(None))
        self.page_animation.start()
        if index == 1: self.render_ai(force=True)

    def run_async(self, function, success=None):
        worker = Worker(function)
        self.active_workers.add(worker)
        def finished(*args):
            self.active_workers.discard(worker)
        if success: worker.signals.done.connect(lambda _: self.statusBar().showMessage(success, 3500))
        worker.signals.done.connect(lambda _: self.refresh_ui(force=True))
        worker.signals.done.connect(finished)
        worker.signals.failed.connect(lambda message: QMessageBox.warning(self, '操作未完成', message))
        worker.signals.failed.connect(finished)
        self.thread_pool.start(worker)

    def check_updates(self):
        if self.checking_updates:
            return
        self.checking_updates = True
        worker = Worker(updates.check_for_update)
        self.active_workers.add(worker)
        def finished(*args):
            self.checking_updates = False
            self.active_workers.discard(worker)
        worker.signals.done.connect(self.show_update)
        worker.signals.done.connect(finished)
        worker.signals.failed.connect(lambda message: self.statusBar().showMessage('更新检查暂不可用，将稍后重试', 5000))
        worker.signals.failed.connect(finished)
        self.thread_pool.start(worker)

    def show_update(self, info):
        self.update_info = info
        self.update_button.setVisible(bool(info))
        if info:
            self.update_button.setText('↑ 更新可用' if info['ready'] else '↑ 发现更新 · 打包中')
            self.update_button.setToolTip('点击查看新版与下载说明')

    def open_update(self):
        info = self.update_info
        if not info:
            return
        if info['ready']:
            message = '新版已准备好。下载发布页中的 NJU-Homework-Monitor.exe，关闭当前程序后替换原 EXE，保留同目录的 data 文件夹即可保留设置和作业数据。'
        else:
            message = '发现新的代码更新，安装包尚未发布。可在构建页面查看进度，程序会自动再次检查。'
        dialog = QMessageBox(self)
        dialog.setWindowTitle('应用更新')
        dialog.setText(message)
        open_button = dialog.addButton('打开下载页' if info['ready'] else '查看构建进度', QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton('稍后', QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        if dialog.clickedButton() == open_button:
            QDesktopServices.openUrl(QUrl(info['url']))

    def select_task_filter(self, mode):
        self.task_filter.setCurrentIndex(self.task_filter.findData(mode))
        self.render_tasks(force=True)

    def refresh_ui(self, force=False):
        self.snapshot = storage.read('snapshot.json', {'courses': [], 'tasks': [], 'errors': []})
        self.settings = storage.settings()
        excluded, included = set(self.settings['excluded_courses']), set(self.settings['included_courses'])
        for course in self.snapshot.get('courses', []):
            course['monitored'] = course['id'] not in excluded and (course.get('current', False) or course['id'] in included)
        state = backend.state.copy()
        connected = state.get('phase') in ('connected', 'partial', 'syncing') and self.snapshot.get('last_sync')
        self.connection.setText(('●  已连接' if connected else '●  等待连接'))
        self.connection.setStyleSheet('color:#449078' if connected else '')
        self.status_label.setText(state.get('message', '准备同步'))
        self.sync_button.setEnabled(state.get('phase') != 'syncing')
        tasks = self.snapshot.get('tasks', [])
        pending_count = sum(matches_filter(task, 'pending') for task in tasks)
        self.nav_buttons[0].setText(f'▦   作业总览                         {pending_count}')
        self.nav_buttons[1].setText(f'✦   AI 任务                       {len(bridge.jobs_snapshot())}')
        last_sync = self.snapshot.get('last_sync')
        sync_text = format_date(last_sync) if last_sync else '尚未同步'
        self.sync_info.setText(f'●  后台监控\n{sync_text}')
        course_value = self.course_filter.currentData()
        course_names = sorted({(task.get('course_id'), task.get('course')) for task in tasks if task.get('course_id') and task.get('course')}, key=lambda item: item[1])
        wanted = [('全部课程', 'all')] + [(name, course_id) for course_id, name in course_names]
        existing = [(self.course_filter.itemText(i), self.course_filter.itemData(i)) for i in range(self.course_filter.count())]
        if existing != wanted:
            self.course_filter.blockSignals(True); self.course_filter.clear()
            for name, value in wanted: self.course_filter.addItem(name, value)
            index = self.course_filter.findData(course_value)
            self.course_filter.setCurrentIndex(max(0, index)); self.course_filter.blockSignals(False)
        self.render_stats()
        self.render_tasks(force)
        self.render_courses(force)
        self.render_ai(force)
        self.render_settings()

    def render_stats(self):
        tasks = self.snapshot.get('tasks', [])
        now = datetime.now().astimezone()
        pending = [task for task in tasks if task.get('status') in ('pending', 'draft')]
        upcoming = []
        for task in pending:
            due = deadline(task)
            if due and due > now: upcoming.append((due, task))
        if upcoming:
            due, task = min(upcoming, key=lambda item: item[0])
            hours = max(0, int((due - now).total_seconds() // 3600))
            countdown = f'{hours // 24} 天 {hours % 24} 小时' if hours >= 24 else f'{hours} 小时'
            self.next_deadline.setText(f'{task.get("title", "未命名作业")}\n\n{countdown} 后截止\n{task.get("course", "")}')
        else:
            self.next_deadline.setText('暂时没有临近截止的作业。\n\n可以安心安排下一件事。')
        values = {mode: sum(matches_filter(task, mode, now) for task in tasks)
                  for mode in self.stat_labels}
        for key, value in values.items(): self.stat_labels[key].setText(str(value))

    @staticmethod
    def clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def render_tasks(self, force=False):
        mode = self.task_filter.currentData()
        for key, button in self.stat_buttons.items():
            button.setChecked(key == mode)
        now = datetime.now().astimezone()
        tasks = self.snapshot.get('tasks', [])
        tasks = [task for task in tasks if matches_filter(task, mode, now)]
        course_id = self.course_filter.currentData()
        if course_id and course_id != 'all': tasks = [task for task in tasks if task.get('course_id') == course_id]
        query = self.task_search.text().strip().casefold()
        if query: tasks = [task for task in tasks if query in f"{task.get('title', '')} {task.get('course', '')}".casefold()]
        jobs = bridge.jobs_snapshot()
        signature = json.dumps([tasks, [display_status(task, now) for task in tasks], jobs, mode, course_id, query], ensure_ascii=False, default=str)
        if not force and self.signatures.get('tasks') == signature: return
        self.signatures['tasks'] = signature
        scroll_value = self.task_scroll.verticalScrollBar().value()
        self.clear_layout(self.task_layout)
        for task in tasks:
            card = TaskCard(task, jobs.get(task['id']))
            card.details.connect(self.show_task)
            card.launch_ai.connect(self.launch_ai)
            self.task_layout.addWidget(card)
        if not tasks:
            empty = QLabel('⌁\n\n这里暂时没有符合条件的作业。')
            empty.setObjectName('emptyState'); empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.task_layout.addWidget(empty)
        self.task_layout.addStretch()
        QTimer.singleShot(0, lambda: self.task_scroll.verticalScrollBar().setValue(scroll_value))

    def render_courses(self, force=False):
        courses = self.snapshot.get('courses', [])
        signature = json.dumps([courses, [(t.get('course_id'), t.get('status')) for t in self.snapshot.get('tasks', [])]], ensure_ascii=False)
        if not force and self.signatures.get('courses') == signature: return
        self.signatures['courses'] = signature
        scroll_value = self.course_scroll.verticalScrollBar().value()
        self.clear_layout(self.course_layout)
        for index, course in enumerate(courses):
            count = len([t for t in self.snapshot.get('tasks', []) if t.get('course_id') == course['id'] and t.get('status') in ('pending', 'draft')])
            card = CourseCard(course, count)
            card.toggled.connect(self.toggle_course)
            self.course_layout.addWidget(card, index // 3, index % 3)
        if courses:
            self.course_layout.setRowStretch((len(courses) + 2) // 3, 1)
        QTimer.singleShot(0, lambda: self.course_scroll.verticalScrollBar().setValue(scroll_value))

    def render_ai(self, force=False):
        job_map = bridge.jobs_snapshot()
        jobs = sorted(job_map.values(), key=lambda job: job.get('created_at', 0), reverse=True)
        files = bridge.workspace_files(self.selected_job) if self.selected_job else []
        signature = json.dumps([jobs, self.selected_job, files], ensure_ascii=False, default=str)
        if not force and self.signatures.get('ai') == signature: return
        self.signatures['ai'] = signature
        current = self.selected_job
        list_signature = [(job.get('task_id'), job.get('state'), job.get('title'), job.get('course'), len(job.get('pending_prompts', []))) for job in jobs]
        if force or self.signatures.get('ai_list') != list_signature:
            self.signatures['ai_list'] = list_signature
            self.job_list.blockSignals(True)
            self.job_list.clear()
            for job in jobs:
                queued = len(job.get('pending_prompts', []))
                suffix = f' · 排队 {queued}' if queued else ''
                item = QListWidgetItem(f"{job.get('title', 'AI 任务')}\n{job.get('course', '')} · {JOB_TEXT.get(job.get('state'), job.get('state', ''))}{suffix}")
                item.setData(Qt.ItemDataRole.UserRole, job['task_id'])
                self.job_list.addItem(item)
                if job['task_id'] == current: self.job_list.setCurrentItem(item)
            if not current and jobs:
                self.selected_job = jobs[0]['task_id']; self.job_list.setCurrentRow(0)
            self.job_list.blockSignals(False)
        job = job_map.get(self.selected_job)
        if not job:
            self.job_progress.hide()
            self.job_title.setText('选择一个 AI 任务'); self.job_state.setText('等待任务')
            self.job_output.setPlainText('✦\n\nAI 输出会同步到这里\n\n启动作业任务后，可以留在本页查看进度并继续对话。')
            for button in (self.stop_job, self.reconnect_job_button, self.send_job, self.attach_button): button.setEnabled(False)
            for action in (self.menu_open_workspace, self.menu_copy_id): action.setEnabled(False)
            self.job_input.setEnabled(False)
            self.clear_attachments_button.setEnabled(False)
            self.job_files.clear()
            return
        self.job_title.setText(job.get('title', 'AI 任务')); self.job_state.setText(JOB_TEXT.get(job.get('state'), job.get('state', '')))
        blocks = []
        for entry in job.get('entries', [])[-200:]: blocks.append({'user': '你', 'assistant': 'AI', 'tool': '工具'}.get(entry.get('role'), 'AI') + '\n' + entry.get('text', ''))
        if job.get('state') in ('starting', 'running'): blocks.append('处理动态\n' + job.get('activity', job.get('message', '正在处理…')))
        bar = self.job_output.verticalScrollBar()
        follow = bar.maximum() - bar.value() < 30
        old_scroll = bar.value()
        self.job_output.setPlainText('\n\n'.join(blocks) or job.get('message', '等待输出'))
        bar.setValue(bar.maximum() if follow else old_scroll)
        running = job.get('state') in ('starting', 'running')
        self.job_progress.setVisible(running or job.get('state') == 'stopping')
        self.stop_job.setEnabled(running)
        self.reconnect_job_button.setEnabled(not running and job.get('state') != 'stopping')
        self.menu_open_workspace.setEnabled(bool(job.get('workspace')))
        self.menu_copy_id.setEnabled(bool(job.get('task_id')))
        self.send_job.setEnabled(job.get('state') != 'stopping')
        self.send_job.setText('加入队列 ↗' if running else '发送 ↗')
        self.job_input.setEnabled(True)
        self.attach_button.setEnabled(True)
        queued = len(job.get('pending_prompts', []))
        self.job_info.setText(f"课程\n{job.get('course', '—')}\n\n截止时间\n{format_date(job.get('due'))}\n\n使用模型\n{job.get('model', '未配置')}\n\n当前状态\n{job.get('message', '—')}\n\n排队消息\n{queued} 条")
        selected_path = self.job_files.currentItem().data(Qt.ItemDataRole.UserRole) if self.job_files.currentItem() else None
        self.job_files.clear()
        for file in files:
            size = file['size']
            size_text = f'{size / 1024:.1f} KB' if size >= 1024 else f'{size} B'
            item = QListWidgetItem(f"{file['name']}\n{size_text}")
            item.setData(Qt.ItemDataRole.UserRole, file['path'])
            self.job_files.addItem(item)
            if file['path'] == selected_path: self.job_files.setCurrentItem(item)

    def render_settings(self):
        widgets = ((self.monitoring, 'monitoring'), (self.notifications, 'notifications'), (self.auto_login, 'auto_login_window'))
        for widget, key in widgets:
            checked = bool(self.settings.get(key))
            widget.blockSignals(True); widget.setChecked(checked); widget._set_knob_position(21 if checked else 3); widget.blockSignals(False)
        starts = autostart()
        self.auto_start.blockSignals(True); self.auto_start.setChecked(starts); self.auto_start._set_knob_position(21 if starts else 3); self.auto_start.blockSignals(False)
        caps = capabilities(); self.auto_start.setEnabled(caps['autostart']); self.notifications.setEnabled(caps['notifications'])
        index = self.interval.findData(self.settings.get('interval_minutes', 30))
        self.interval.blockSignals(True); self.interval.setCurrentIndex(max(0, index)); self.interval.blockSignals(False)
        if not self.username.hasFocus():
            try: credential = storage.read('credentials.dpapi', {}, secret=True)
            except Exception: credential = {}
            self.username.setText(credential.get('username', ''))

    def show_task(self, task_id):
        task = next((t for t in self.snapshot.get('tasks', []) if t['id'] == task_id), None)
        if not task: return
        dialog = QDialog(self); dialog.setWindowTitle('作业详情'); dialog.resize(650, 520)
        layout = QVBoxLayout(dialog)
        title = QLabel(task.get('title', '作业')); title.setObjectName('pageTitle')
        description = QTextBrowser(); description.setPlainText(task.get('description') or '请打开原作业查看详细要求。')
        buttons = QHBoxLayout()
        source = QPushButton('打开原作业 ↗'); source.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(task.get('url', ''))))
        ai = QPushButton('✦ AI 一键完成'); ai.setObjectName('primaryButton'); ai.clicked.connect(lambda: (dialog.accept(), self.launch_ai(task_id)))
        buttons.addWidget(source); buttons.addStretch(); buttons.addWidget(ai)
        layout.addWidget(QLabel(task.get('course', ''))); layout.addWidget(title); layout.addWidget(QLabel(format_date(task.get('due')))); layout.addWidget(description, 1); layout.addLayout(buttons)
        dialog.exec()

    def launch_ai(self, task_id):
        task = next((t for t in self.snapshot.get('tasks', []) if t['id'] == task_id), None)
        if not task: return
        try:
            bridge.launch(task); self.selected_job = task_id; self.switch_page(1); self.refresh_ui(force=True)
            self.statusBar().showMessage('AI 已在后台启动', 3500)
        except Exception as error:
            QMessageBox.warning(self, 'AI 未启动', str(error))

    def select_job(self, current, previous=None):
        if current:
            self.selected_job = current.data(Qt.ItemDataRole.UserRole)
            self.pending_attachments = []
            self.update_attachment_label()
            self.render_ai(force=True)

    def update_attachment_label(self):
        count = len(self.pending_attachments)
        if not count:
            self.attachment_label.setText('未附加文件')
            self.clear_attachments_button.setEnabled(False)
        else:
            names = '、'.join(item['name'] for item in self.pending_attachments)
            self.attachment_label.setText(f'已附加 {count} 个文件：{names}')
            self.clear_attachments_button.setEnabled(True)

    def attach_files(self):
        if not self.selected_job:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, '选择要上传的图片或文件')
        if not paths:
            return
        try:
            records = bridge.attach_files(self.selected_job, paths)
            self.pending_attachments.extend(records)
            self.update_attachment_label()
            self.statusBar().showMessage(f'已附加 {len(records)} 个文件', 3000)
        except Exception as error:
            QMessageBox.warning(self, '附件添加失败', str(error))

    def clear_attachments(self):
        self.pending_attachments = []
        self.update_attachment_label()

    def continue_job(self):
        text = self.job_input.toPlainText().strip()
        if not self.selected_job or not text: return
        try:
            bridge.continue_job(self.selected_job, text, self.pending_attachments)
            self.job_input.clear(); self.pending_attachments = []; self.update_attachment_label()
            self.refresh_ui(force=True)
        except Exception as error: QMessageBox.warning(self, '发送失败', str(error))

    def interrupt_job(self):
        task_id = self.selected_job
        if task_id:
            self.stop_job.setEnabled(False)
            self.run_async(lambda: bridge.interrupt(task_id), '停止请求已处理')

    def reconnect_job(self):
        task_id = self.selected_job
        if task_id:
            try:
                bridge.reconnect(task_id)
                self.render_ai(force=True)
            except Exception as error:
                QMessageBox.warning(self, '继续失败', str(error))

    def open_job_workspace(self):
        if self.selected_job: self.run_async(lambda: bridge.open_workspace(self.selected_job))

    def copy_job_id(self):
        job = bridge.get_job(self.selected_job) or {}
        value = job.get('task_id')
        if value:
            QApplication.clipboard().setText(str(value))
            self.statusBar().showMessage('任务 ID 已复制', 2500)

    def open_generated_file(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and Path(path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def toggle_course(self, course_id, enabled):
        excluded, included = set(self.settings['excluded_courses']), set(self.settings['included_courses'])
        if enabled: excluded.discard(course_id); included.add(course_id)
        else: excluded.add(course_id); included.discard(course_id)
        storage.write('settings.json', self.settings | {'excluded_courses': sorted(excluded), 'included_courses': sorted(included)})
        backend.state['next_sync'] = 0; backend.wake.set(); self.statusBar().showMessage('课程监控范围已更新', 3000)
        self.refresh_ui(force=True)

    def save_setting(self, key, value):
        storage.write('settings.json', storage.settings() | {key: value})
        if key == 'monitoring': backend.state['next_sync'] = 0
        backend.wake.set()

    def save_autostart(self, enabled):
        try: autostart(enabled)
        except Exception as error: QMessageBox.warning(self, '设置失败', str(error))

    def save_credentials(self):
        username, password = self.username.text().strip(), self.password.text()
        if not username.isdigit() or not password:
            QMessageBox.warning(self, '信息不完整', '请输入数字学号和密码。'); return
        storage.write('credentials.dpapi', {'username': username, 'password': password}, secret=True)
        self.password.clear(); self.statusBar().showMessage('凭据已加密保存在本机', 3500)

    def configure_model(self):
        try:
            cfg = configuration()
            profiles = model_profiles()
        except Exception as error:
            QMessageBox.warning(self, '无法读取模型配置', str(error))
            return
        dialog = QDialog(self); dialog.setWindowTitle('AI 模型设置'); dialog.setMinimumWidth(620)
        form = QFormLayout(dialog); form.setSpacing(14)
        hint = QLabel('选择常见服务商，自动填写 API 地址与模型；也可自定义。\n作业内容与读取的附件会发送给你配置的模型服务。')
        hint.setWordWrap(True); form.addRow(hint)
        provider = QComboBox(); provider.setObjectName('modelProvider')
        for name, preset in PRESETS.items(): provider.addItem(preset['label'], name)
        provider.setCurrentIndex(max(0, provider.findData(cfg['provider'])))
        form.addRow('模型服务商', provider)
        url = QLineEdit(cfg['base_url']); url.setPlaceholderText('https://服务地址/v1 或 http://localhost:端口/v1')
        url.setObjectName('modelBaseUrl')
        model = QComboBox(); model.setEditable(True); model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        model.setObjectName('modelName'); model.addItems(PRESETS[cfg['provider']]['models']); model.setCurrentText(cfg['model'])
        model.lineEdit().setPlaceholderText('选择预设，或直接输入模型 ID')
        key = QLineEdit(cfg['api_key']); key.setEchoMode(QLineEdit.EchoMode.Password)
        key.setObjectName('modelApiKey')
        key.setPlaceholderText('API Key（无需鉴权的本地服务可留空）')
        stream = ToggleSwitch(); stream.setChecked(cfg['stream'])
        vision = ToggleSwitch(); vision.setChecked(cfg['vision'])
        timeout = QLineEdit(str(cfg['timeout'])); rounds = QLineEdit(str(cfg['max_rounds']))
        timeout.setObjectName('modelTimeout'); rounds.setObjectName('modelRounds')
        form.addRow('API 地址', url); form.addRow('模型名称', model); form.addRow('API Key', key)
        provider_note = QLabel(); provider_note.setObjectName('muted'); provider_note.setWordWrap(True)
        form.addRow(provider_note)
        form.addRow('流式输出', stream); form.addRow('视觉输入（模型需支持）', vision)
        form.addRow('请求超时 / 秒', timeout); form.addRow('工具循环上限', rounds)
        note = QLabel('各服务商的配置分别加密保存；点击保存后切换生效。\n地址、模型名均可修改；模型是否可用以账号权限和连接测试为准。')
        note.setObjectName('muted'); note.setWordWrap(True); form.addRow(note)
        actions = QHBoxLayout()
        test = QPushButton('测试连接与工具调用'); save = QPushButton('保存配置'); save.setObjectName('primaryButton')
        actions.addWidget(test); actions.addStretch(); actions.addWidget(save); form.addRow(actions)

        def values():
            return {'provider': provider.currentData(), 'base_url': url.text(), 'model': model.currentText(), 'api_key': key.text().strip(),
                    'stream': stream.isChecked(), 'vision': vision.isChecked(),
                    'timeout': timeout.text(), 'max_rounds': rounds.text()}

        selected_provider = cfg['provider']

        def update_provider_note():
            provider_note.setText(PRESETS[provider.currentData()]['note'])

        def select_provider():
            nonlocal selected_provider
            # Remember unfinished edits within this dialog; save only when the user clicks Save.
            previous = values(); previous['provider'] = selected_provider
            profiles[selected_provider] = previous
            selected_provider = provider.currentData()
            chosen = profiles.get(selected_provider) or preset_configuration(selected_provider)
            url.setText(chosen['base_url']); key.setText(chosen['api_key'])
            model.clear(); model.addItems(PRESETS[selected_provider]['models']); model.setCurrentText(chosen['model'])
            stream.setChecked(chosen['stream']); vision.setChecked(chosen['vision'])
            timeout.setText(str(chosen['timeout'])); rounds.setText(str(chosen['max_rounds']))
            update_provider_note()

        update_provider_note()
        provider.currentIndexChanged.connect(select_provider)

        def save_model():
            try:
                save_configuration(values())
                dialog.accept()
                self.statusBar().showMessage('模型配置已加密保存', 3500)
            except Exception as error:
                QMessageBox.warning(dialog, '配置无效', str(error))

        def test_model():
            from ai_tools import SCHEMAS
            try:
                client = ModelClient(values())
            except Exception as error:
                QMessageBox.warning(dialog, '配置无效', str(error)); return
            def check():
                answer = client.complete([{'role': 'user', 'content': '请调用 list_files 工具，path 为 .。不要直接回复文字。'}],
                                         [SCHEMAS[0]], threading.Event())
                if not any(c['function']['name'] == 'list_files' for c in answer.get('tool_calls', [])):
                    raise RuntimeError('模型可以连接，但没有返回工具调用。请确认模型和接口支持 tools。')
            self.run_async(check, '模型连接与工具调用测试通过')

        save.clicked.connect(save_model); test.clicked.connect(test_model)
        dialog.exec()

    def closeEvent(self, event):
        self.timer.stop()
        self.ai_timer.stop()
        self.update_timer.stop()
        backend.stop.set(); backend.wake.set(); bridge.close(); event.accept()


def _install_excepthook():
    import traceback
    log_path = storage.DATA / 'app.log'

    def handle(exc_type, exc_value, exc_tb):
        text = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(text + '\n')
        except OSError:
            pass
        traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = handle


def main():
    _install_excepthook()
    if '--login-window' in sys.argv:
        from login_window import run_login
        run_login(); return 0
    if '--read-homework' in sys.argv:
        sys.argv.remove('--read-homework')
        from read_homework import main as read_main
        read_main(); return 0
    if '--code-worker' in sys.argv:
        index = sys.argv.index('--code-worker')
        from code_runner import main as code_main
        code_main(sys.argv[index + 1:]); return 0
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName('课后')
    instance_lock = acquire_instance_lock()
    if instance_lock is None:
        QMessageBox.information(None, '课后正在运行', '作业监控已经在运行，请先关闭已有实例。')
        return 0
    qt_app.aboutToQuit.connect(lambda: release_instance_lock(instance_lock))
    window = MainWindow()
    window.show()
    return qt_app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
