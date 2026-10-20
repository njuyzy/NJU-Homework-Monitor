"""GitHub release checks, verified downloads, and Windows update handover."""
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

REPOSITORY = 'njuyzy/NJU-Homework-Monitor'
WEB_URL = f'https://github.com/{REPOSITORY}'
API_URL = f'https://api.github.com/repos/{REPOSITORY}'
BRANCH = 'main'
ASSET_NAME = 'NJU-Homework-Monitor.exe'
MAX_INSTALLER_SIZE = 512 * 1024 * 1024


class UpdateCancelled(Exception):
    pass


def can_auto_update():
    return os.name == 'nt' and bool(getattr(sys, 'frozen', False))


def consume_restart_argument(argv):
    if '--update-ready' not in argv:
        return None
    index = argv.index('--update-ready')
    if index + 1 >= len(argv):
        raise ValueError('更新启动参数缺失。')
    path = argv[index + 1]
    del argv[index:index + 2]
    return path


def mark_restart_ready(path):
    acknowledgement = Path(path).resolve()
    directory = acknowledgement.parent
    if (acknowledgement.name != 'restarted' or not directory.name.startswith('.nju-update-') or
            directory.parent != Path(sys.executable).resolve().parent):
        raise ValueError('更新启动回执路径无效。')
    acknowledgement.write_text('ready', encoding='ascii')


def local_revision():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    try:
        revision = json.loads((root / 'build-info.json').read_text(encoding='utf-8-sig'))['revision']
    except (OSError, ValueError, KeyError, TypeError):
        if getattr(sys, 'frozen', False):
            return None
        try:
            revision = subprocess.check_output(
                ['git', 'rev-parse', 'HEAD'], cwd=root, text=True, timeout=5,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.SubprocessError):
            return None
    return revision if isinstance(revision, str) and re.fullmatch(r'[0-9a-f]{40}', revision) else None


def check_for_update(revision=None):
    revision = revision or local_revision()
    if not revision:
        return None
    with requests.Session() as session:
        session.headers.update({'Accept': 'application/vnd.github+json',
                                'User-Agent': 'NJU-Homework-Monitor-update-check'})

        def get(path):
            response = session.get(API_URL + path, timeout=(5, 10))
            response.raise_for_status()
            return response.json()

        comparison = get(f'/compare/{revision}...{BRANCH}')
        if comparison.get('status') != 'ahead':
            return None
        latest = comparison['commits'][-1]['sha']
        # Read the actual branch tip: compare results can be truncated after 250 commits.
        if comparison.get('total_commits', 0) > len(comparison['commits']):
            latest = get(f'/commits/{BRANCH}')['sha']
        if not re.fullmatch(r'[0-9a-f]{40}', latest):
            raise ValueError('更新版本编号无效')
        result = {'revision': latest, 'ready': False,
                  'url': WEB_URL + '/actions/workflows/release.yml'}
        tag = 'build-' + latest
        response = session.get(API_URL + '/releases/tags/' + tag, timeout=(5, 10))
        if response.status_code == 404:
            return result
        response.raise_for_status()
        release = response.json()
        asset = next((asset for asset in release.get('assets', [])
                      if asset.get('name') == ASSET_NAME and asset.get('state') == 'uploaded'), None)
        if not release.get('draft') and asset:
            result.update(ready=True, url=WEB_URL + '/releases/tag/' + tag,
                          asset={key: asset.get(key) for key in ('name', 'size', 'digest')})
        return result


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise UpdateCancelled('更新已取消。')


def cleanup_download(package):
    target = Path(package['target']).resolve()
    work = Path(package['work_dir']).resolve()
    if work.parent != target.parent or not work.name.startswith('.nju-update-'):
        raise ValueError('更新缓存目录无效。')
    shutil.rmtree(work, ignore_errors=True)


def _verify_executable(path):
    with path.open('rb') as stream:
        header = stream.read(64)
        if len(header) < 64 or header[:2] != b'MZ':
            raise ValueError('下载内容不是 Windows EXE，未替换当前程序。')
        offset = struct.unpack_from('<I', header, 60)[0]
        if not 64 <= offset <= path.stat().st_size - 4:
            raise ValueError('下载的 EXE 文件头无效。')
        stream.seek(offset)
        if stream.read(4) != b'PE\0\0':
            raise ValueError('下载的 EXE 文件头无效。')


def download_update(info, target, progress=lambda done, total: None, cancel=None):
    """Stage a release beside the running EXE. Never touch the installed file."""
    revision = info.get('revision', '')
    asset = info.get('asset') or {}
    digest = asset.get('digest') or ''
    size = asset.get('size')
    if not info.get('ready') or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('更新版本无效，请重新检查更新。')
    if asset.get('name') != ASSET_NAME or not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest):
        raise ValueError('发布文件缺少 SHA-256 校验信息，请重新检查更新。')
    if type(size) is not int or not 64 <= size <= MAX_INSTALLER_SIZE:
        raise ValueError('发布文件大小无效。')
    target = Path(target).resolve()
    if target.suffix.lower() != '.exe' or not target.is_file():
        raise ValueError('只能更新当前安装的 EXE 文件。')
    _check_cancel(cancel)
    work = Path(tempfile.mkdtemp(prefix='.nju-update-', dir=target.parent))
    package = {'target': str(target), 'work_dir': str(work), 'staged': str(work / ASSET_NAME),
               'sha256': digest[7:].lower(), 'revision': revision}
    temporary = work / 'download.part'
    # Pin the download to our repository and the exact revision shown to the
    # user; do not execute a URL supplied by chat messages or an arbitrary host.
    url = f'{WEB_URL}/releases/download/build-{revision}/{ASSET_NAME}'
    try:
        downloaded, checksum = 0, hashlib.sha256()
        deadline = time.monotonic() + 900
        with requests.Session() as session:
            session.headers.update({'User-Agent': 'NJU-Homework-Monitor-updater'})
            with session.get(url, stream=True, timeout=(10, 30)) as response:
                response.raise_for_status()
                with temporary.open('xb') as stream:
                    progress(0, size)
                    for chunk in response.iter_content(128 * 1024):
                        _check_cancel(cancel)
                        if time.monotonic() > deadline:
                            raise TimeoutError('更新下载超时，请稍后重试。')
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > size:
                            raise ValueError('下载文件大小与发布记录不符。')
                        checksum.update(chunk)
                        stream.write(chunk)
                        progress(downloaded, size)
                    stream.flush()
                    os.fsync(stream.fileno())
        _check_cancel(cancel)
        if downloaded != size:
            raise ValueError('更新下载不完整，请重新下载。')
        if checksum.hexdigest() != package['sha256']:
            raise ValueError('更新文件 SHA-256 校验失败，当前程序未被替换。')
        _verify_executable(temporary)
        temporary.replace(package['staged'])
        return package
    except Exception:
        cleanup_download(package)
        raise


def launch_installer(package, log_path, cancel=None):
    """Start an independent helper and wait for its ready acknowledgement."""
    _check_cancel(cancel)
    work = Path(package['work_dir']).resolve()
    target = Path(package['target']).resolve()
    staged = Path(package['staged']).resolve()
    if work.parent != target.parent or not work.name.startswith('.nju-update-') or staged.parent != work:
        raise ValueError('更新文件路径无效。')
    source = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'update_installer.ps1'
    helper = work / 'install.ps1'
    shutil.copyfile(source, helper)
    plan = work / 'plan.json'
    plan.write_text(json.dumps(package | {'parent_pid': os.getpid(), 'log_path': str(Path(log_path).resolve())},
                               ensure_ascii=False), encoding='utf-8')
    powershell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        process = subprocess.Popen([str(powershell), '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
                                    '-ExecutionPolicy', 'Bypass', '-File', str(helper), '-PlanPath', str(plan)],
                                   cwd=work, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                   close_fds=True)
    except OSError as error:
        cleanup_download(package)
        raise RuntimeError('无法启动 Windows 更新程序，当前应用保持运行。') from error
    deadline = time.monotonic() + 15
    try:
        while not (work / 'ready').is_file():
            _check_cancel(cancel)
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('无法启动更新程序，当前应用保持运行；详情见 data/update.log。')
            time.sleep(.05)
        _check_cancel(cancel)
    except Exception:
        (work / 'abort').touch()
        # The helper checks abort while waiting for the original process. Do
        # not remove its script directory while PowerShell is still using it.
        raise
