"""Restricted Python worker used by the AI code execution tool."""
import os
import runpy
import socket
import subprocess
import sys
from pathlib import Path


def main(argv=None):
    argv = list(argv or sys.argv[1:])
    if len(argv) < 2:
        raise SystemExit('usage: code_runner.py WORKSPACE SOURCE [args...]')
    workspace, source = Path(argv[0]).resolve(), Path(argv[1]).resolve()
    runner_file = Path(__file__).resolve()
    if not source.is_file() or not source.is_relative_to(workspace):
        raise SystemExit('source must be a file inside the workspace')
    readable_roots = [workspace, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()]
    if getattr(sys, '_MEIPASS', None):
        readable_roots.append(Path(sys._MEIPASS).resolve())

    def inside(path, roots):
        try:
            resolved = Path(path).resolve()
        except (OSError, TypeError, ValueError):
            return False
        return any(resolved.is_relative_to(root) for root in roots)

    def audit(event, args):
        if event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
            path = args[0]
            mode = str(args[1]) if len(args) > 1 else 'r'
            writing = any(flag in mode for flag in ('w', 'a', 'x', '+'))
            readable = inside(path, readable_roots) or (not writing and Path(path).resolve() == runner_file)
            if writing and not inside(path, [workspace]) or not writing and not readable:
                raise PermissionError('代码只能写入任务目录，不能读取任务目录外的普通文件。')
        if event in ('socket.connect', 'socket.bind', 'socket.getaddrinfo', 'subprocess.Popen',
                     'os.system', 'os.posix_spawn', 'os.spawn'):
            raise PermissionError('Python 代码执行工具禁止网络和子进程。')
        if event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.chown',
                     'os.truncate', 'os.utime') and args:
            if not inside(args[0], [workspace]):
                raise PermissionError('代码只能修改任务目录内的文件。')
        if event in ('os.rename', 'os.replace') and len(args) >= 2:
            if not inside(args[0], [workspace]) or not inside(args[1], [workspace]):
                raise PermissionError('代码只能移动任务目录内的文件。')
        if event in ('os.link', 'os.symlink') and len(args) >= 2:
            if not inside(args[0], [workspace]) or not inside(args[1], [workspace]):
                raise PermissionError('代码只能在任务目录内创建链接。')
        if event in ('os.chdir', 'os.listdir', 'os.scandir') and args and args[0] is not None:
            if not inside(args[0], readable_roots):
                raise PermissionError('代码不能浏览任务目录外的位置。')
        if event in ('os.startfile', 'os.startfile/2'):
            raise PermissionError('Python 代码执行工具禁止启动外部程序。')
        if event == 'import' and args and args[0] in ('ctypes', 'winreg', '_winapi'):
            raise PermissionError('代码执行工具禁止加载底层系统接口。')

    sys.addaudithook(audit)
    socket.socket = _blocked
    subprocess.Popen = _blocked
    os.chdir(workspace)
    sys.argv = [str(source), *argv[2:]]
    sys.path.insert(0, str(source.parent))
    runpy.run_path(str(source), run_name='__main__')


def _blocked(*args, **kwargs):
    raise PermissionError('Python 代码执行工具禁止网络和子进程。')


if __name__ == '__main__':
    main()
