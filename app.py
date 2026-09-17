"""Backend sync and monitoring logic for the NJU Homework Monitor desktop app."""
import threading
import time
from datetime import datetime

import storage
from moodle import Moodle, LoginRequired, TZ
from windows_integration import capabilities, login_process, notify

sync_lock = threading.Lock()
stop = threading.Event()
wake = threading.Event()
login_handle = None
login_lock = threading.Lock()
state = {'phase': 'idle', 'message': '准备同步', 'last_attempt': None, 'next_sync': None,
         'notification_error': None, 'last_error': None}


def open_login_once(manual=False):
    global login_handle
    with login_lock:
        if login_handle is not None and login_handle.poll() is None:
            return False
        prompt = storage.read('login-prompt.json', {})
        if not manual and prompt.get('prompted'):
            return False
        storage.write('login-status.json', {'state': 'opening', 'message': '登录已失效，正在打开学校登录窗口…'})
        login_handle = login_process()
        storage.write('login-prompt.json', {'prompted': True, 'at': time.time()})
        return True


def reminder_bucket(task, now, hours):
    if task['status'] not in ('pending', 'draft') or task.get('stale'):
        return None
    if not task.get('due'):
        return 'no-date:' + now.strftime('%Y-%m-%d')
    due = datetime.fromisoformat(task['due'])
    remaining = (due - now).total_seconds() / 3600
    if remaining <= 0:
        return 'overdue:' + now.strftime('%Y-%m-%d')
    windows = [h for h in hours if remaining <= h]
    return str(min(windows)) if windows else None


def send_reminders(snapshot):
    cfg = storage.settings()
    if not cfg['monitoring'] or not cfg['notifications'] or not capabilities()['notifications'] or not snapshot.get('last_sync'):
        return
    if state['phase'] in ('error', 'login_required'):
        return
    now = datetime.now(TZ)
    if (now - datetime.fromisoformat(snapshot['last_sync'])).total_seconds() > cfg['interval_minutes'] * 120:
        return
    ledger = storage.read('reminders.json', {})
    due_items = []
    keys = []
    for task in snapshot.get('tasks', []):
        bucket = reminder_bucket(task, now, cfg['reminder_hours'])
        key = f'{task["id"]}|{task.get("due")}|{bucket}'
        if bucket and key not in ledger:
            due_items.append(task)
            keys.append(key)
    if not due_items:
        return
    first = due_items[0]
    deadline = first.get('due_raw') or '未设置截止时间'
    try:
        notify(f'你有 {len(due_items)} 项未完成作业需要关注', f'{first["course"]} · {first["title"]}\n{deadline}\n打开南大作业提醒查看详情。')
        for key in keys:
            ledger[key] = now.isoformat()
        ledger = {k: v for k, v in ledger.items() if (now - datetime.fromisoformat(v)).days < 120}
        storage.write('reminders.json', ledger)
        state['notification_error'] = None
    except Exception as e:
        state['notification_error'] = f'桌面通知发送失败（{type(e).__name__}），请检查系统通知设置。'


def sync():
    if not sync_lock.acquire(blocking=False):
        return
    state.update(phase='syncing', message='正在连接学习平台…', last_attempt=datetime.now(TZ).isoformat())
    try:
        result = Moodle().collect(lambda m: state.update(message=m))
        previous = storage.read('snapshot.json', {})
        fresh_ids = {t['id'] for t in result['tasks']}
        failed_courses = {e['course_id'] for e in result['errors'] if 'url' not in e}
        failed_urls = {e['url'] for e in result['errors'] if 'url' in e}
        for old in previous.get('tasks', []):
            if old['id'] not in fresh_ids and (old['course_id'] in failed_courses or old['url'] in failed_urls):
                result['tasks'].append(old | {'stale': True})
        result['last_sync'] = datetime.now(TZ).isoformat()
        result['last_full_sync'] = previous.get('last_full_sync') if result['errors'] else result['last_sync']
        storage.write('snapshot.json', result)
        state.update(phase='partial' if result['errors'] else 'connected', last_error=None,
                     message='部分项目读取失败，已保留旧数据。' if result['errors'] else '同步完成，正在守候下一次截止时间。')
        storage.write('login-prompt.json', {'prompted': False})
        send_reminders(result)
    except LoginRequired as e:
        state.update(phase='login_required', message=str(e), last_error='login_required')
        if storage.settings()['auto_login_window']:
            try:
                open_login_once()
            except Exception:
                state['message'] += ' 自动打开窗口失败，请点“连接账号”重试。'
    except Exception as e:
        state.update(phase='error', message=f'同步失败（{type(e).__name__}），保留上次结果；联网后会重试。', last_error=type(e).__name__)
    finally:
        state['next_sync'] = time.time() + storage.settings()['interval_minutes'] * 60
        sync_lock.release()


def monitor():
    last_reminder = 0
    while not stop.is_set():
        cfg = storage.settings()
        login_state = storage.read('login-status.json', {})
        if login_state.get('state') == 'connected':
            storage.write('login-status.json', {'state': 'idle', 'message': '账号已连接'})
            state['next_sync'] = 0
        if cfg['monitoring'] and time.time() >= (state['next_sync'] or 0):
            sync()
        if time.time() - last_reminder >= 60:
            try:
                send_reminders(storage.read('snapshot.json', {}))
            except Exception:
                state['notification_error'] = '提醒检查失败，将在下一次检查时重试。'
            last_reminder = time.time()
        wake.wait(2)
        wake.clear()
