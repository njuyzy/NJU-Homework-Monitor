"""Small OS integration layer with safe fallbacks for Windows, macOS and Linux."""
import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path
import storage

APP_NAME = 'NJU Homework Monitor'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def python_executable(background=False):
    if os.name == 'nt' and background:
        pythonw = Path(sys.executable).with_name('pythonw.exe')
        if pythonw.exists():
            return str(pythonw)
    return sys.executable


def acquire_instance_lock():
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.restype = ctypes.c_void_p
        mutex = kernel.CreateMutexW(None, False, r'Local\NJUHomeworkMonitor8765')
        return None if ctypes.get_last_error() == 183 else (kernel, mutex)
    import fcntl
    lock_file = (storage.DATA / 'service.lock').open('a+b')
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file
    except BlockingIOError:
        lock_file.close()
        return None


def release_instance_lock(handle):
    if not handle:
        return
    if os.name == 'nt':
        kernel, mutex = handle
        kernel.CloseHandle(ctypes.c_void_p(mutex))
    else:
        handle.close()


def capabilities():
    if os.name == 'nt':
        return {'platform': 'windows', 'autostart': True, 'notifications': True, 'browser': 'Edge 或 Chromium'}
    if sys.platform == 'darwin':
        return {'platform': 'macos', 'autostart': False, 'notifications': bool(shutil.which('osascript')), 'browser': 'Chromium'}
    return {'platform': 'linux', 'autostart': False, 'notifications': bool(shutil.which('notify-send')), 'browser': 'Chromium'}


def autostart(enabled=None):
    if os.name != 'nt':
        return False
    import winreg
    name = 'NJUHomeworkMonitor'
    command = f'"{sys.executable}"' if getattr(sys, 'frozen', False) else f'"{python_executable(background=True)}" "{storage.ROOT / "desktop_app.py"}"'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled is True:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
        elif enabled is False:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
        try:
            return winreg.QueryValueEx(key, name)[0] == command
        except FileNotFoundError:
            return False


def notify(title, body):
    if os.name == 'nt':
        from windows_toasts import WindowsToaster, Toast
        toaster = WindowsToaster(APP_NAME)
        toast = Toast()
        toast.text_fields = [title, body]
        toaster.show_toast(toast)
        return
    if sys.platform == 'darwin' and shutil.which('osascript'):
        subprocess.run(['osascript', '-e', 'on run argv', '-e',
                        'display notification item 2 of argv with title item 1 of argv', '-e', 'end run',
                        title, body], check=True, timeout=10)
        return
    if shutil.which('notify-send'):
        subprocess.run(['notify-send', title, body], check=True, timeout=10)
        return
    raise RuntimeError('当前系统未安装可用的桌面通知工具。')


def login_process():
    kwargs = {'cwd': storage.ROOT}
    if os.name == 'nt':
        kwargs['creationflags'] = CREATE_NO_WINDOW
    command = [sys.executable, '--login-window'] if getattr(sys, 'frozen', False) else [python_executable(background=True), str(storage.ROOT / 'login_window.py')]
    return subprocess.Popen(command, **kwargs)


def open_path(path):
    path = str(path)
    if os.name == 'nt':
        os.startfile(path)
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', path])
    elif shutil.which('xdg-open'):
        subprocess.Popen(['xdg-open', path])
    else:
        raise RuntimeError('当前系统没有可用的文件管理器打开命令。')
