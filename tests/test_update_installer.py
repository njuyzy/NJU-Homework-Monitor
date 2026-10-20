"""Exercise real Windows file locks, quoted paths, restart, and rollback."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows executable installer')


@pytest.fixture(scope='module')
def test_executables(tmp_path_factory):
    root = tmp_path_factory.mktemp('update-binaries')
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    source = root / 'compile.ps1'
    source.write_text('''param([string]$Directory)
$old = @'
using System; using System.IO; using System.Threading;
public class OldApp { public static void Main(string[] args) {
if (args.Length == 0) { File.WriteAllText(Environment.GetEnvironmentVariable("NJU_UPDATE_SMOKE_MARKER"), "old"); }
Thread.Sleep(args.Length > 0 ? 500 : 1500);
} }
'@
$next = @'
using System; using System.IO; using System.Threading;
public class NewApp { public static void Main(string[] args) {
File.WriteAllText(Environment.GetEnvironmentVariable("NJU_UPDATE_SMOKE_MARKER"), "new");
if (args.Length == 2 && args[0] == "--update-ready") { File.WriteAllText(args[1], "ready"); }
Thread.Sleep(1500);
} }
'@
$bad = 'public class BadApp { public static void Main() { System.Environment.Exit(3); } }'
Add-Type -TypeDefinition $old -OutputAssembly (Join-Path $Directory 'old.exe') -OutputType WindowsApplication
Add-Type -TypeDefinition $next -OutputAssembly (Join-Path $Directory 'new.exe') -OutputType WindowsApplication
Add-Type -TypeDefinition $bad -OutputAssembly (Join-Path $Directory 'bad.exe') -OutputType WindowsApplication
''', encoding='utf-8-sig')
    subprocess.run([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(source),
                    '-Directory', str(root)], check=True, capture_output=True, timeout=30,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    return root, str(powershell)


@pytest.mark.parametrize('rollback', [False, True])
def test_real_installer_restarts_or_restores_without_changing_data(tmp_path, test_executables, rollback):
    binaries, powershell = test_executables
    root = tmp_path / "桌面 folder & '"
    root.mkdir()
    target = root / "Homework & ' Monitor.exe"
    shutil.copyfile(binaries / 'old.exe', target)
    original = target.read_bytes()
    work = Path(tempfile.mkdtemp(prefix='.nju-update-', dir=root))
    staged = work / 'package.exe'
    shutil.copyfile(binaries / ('bad.exe' if rollback else 'new.exe'), staged)
    expected = staged.read_bytes()
    data = root / 'data'
    data.mkdir()
    (data / 'snapshot.json').write_text('preserved-data', encoding='utf-8')
    marker = root / 'marker.txt'
    env = os.environ.copy()
    env['NJU_UPDATE_SMOKE_MARKER'] = str(marker)
    parent = subprocess.Popen([str(target), '--old'], env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    plan = {'target': str(target), 'staged': str(staged), 'parent_pid': parent.pid,
            'sha256': hashlib.sha256(expected).hexdigest(), 'revision': 'b' * 40,
            'log_path': str(data / 'update.log')}
    (work / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
    helper = Path(__file__).resolve().parents[1] / 'update_installer.ps1'
    process = subprocess.Popen([powershell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                '-File', str(helper), '-PlanPath', str(work / 'plan.json')], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        stdout, stderr = process.communicate(timeout=20)
        parent.wait(timeout=5)
        log = (data / 'update.log').read_text(encoding='utf-8-sig')
        assert process.returncode == (1 if rollback else 0), (log, stdout, stderr)
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert marker.read_text() == ('old' if rollback else 'new'), log
        assert target.read_bytes() == (original if rollback else expected)
        assert (data / 'snapshot.json').read_text() == 'preserved-data'
    finally:
        if process.poll() is None:
            (work / 'abort').touch()
            process.kill()
            process.wait(5)
        parent.wait(timeout=5)
        time.sleep(2)  # Allow the disposable restarted EXE to release its image.
