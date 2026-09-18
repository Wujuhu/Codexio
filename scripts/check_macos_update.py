"""Exercise the frozen updater on an isolated disposable application copy."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

root = Path(__file__).resolve().parents[1] / 'build/checks/macos-updater'
root.mkdir(parents=True, exist_ok=True)
os.environ['CODEXIO_DATA_DIR'] = str(root / 'profile')
os.environ.pop('LOCALAPPDATA', None)

from codexio import __version__, macos_updater as mac
from codexio.update_installer import cancel_job, commit_job, new_job_dir, read_state
from codexio.updates import Release, file_sha256

source = Path(__file__).resolve().parents[1] / 'build/dev/macos'
manifest = json.loads((source / 'latest.json').read_text(encoding='utf-8'))['macos']
assert manifest['size'] == (source / 'Codexio.app.zip').stat().st_size
assert manifest['sha256'] == file_sha256(source / 'Codexio.app.zip')
target = root / 'install/Codexio.app'
assert not target.exists(), 'Use a fresh test directory; never replace a running fixture.'
target.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(['/usr/bin/ditto', str(source / 'Codexio.app'), str(target)], check=True)
directory = new_job_dir()
shutil.copy2(source / 'Codexio.app.zip', directory / 'package.bin')
stop = directory / 'parent-exit'
parent = subprocess.Popen([sys.executable, '-c',
    'import sys,time; from pathlib import Path\nwhile not Path(sys.argv[1]).exists(): time.sleep(0.1)', str(stop)])
receipt = None
try:
    release = Release(manifest['version'], manifest['url'], manifest['sha256'], manifest['size'])
    job = mac.launch_installer(directory, release, threading.Event(),
                               executable=target / 'Contents/MacOS/Codexio',
                               parent_pid=parent.pid, arguments=['--mock'])
    assert read_state(job)['state'] == 'ready'
    assert parent.poll() is None
    commit_job(job)
    stop.touch()
    parent.wait(timeout=10)
    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        state = read_state(job)
        if (job / 'ack.json').exists():
            receipt = json.loads((job / 'ack.json').read_text(encoding='utf-8'))
        if state.get('state') in ('done', 'failed', 'rolled_back', 'unconfirmed'):
            break
        time.sleep(0.2)
    assert state.get('state') == 'done', state
    assert receipt['version'] == manifest['version'] == __version__
    assert not (directory / 'previous.app').exists()
    assert not list(target.parent.glob('*.previous.app'))
    assert not (directory / 'package.bin').exists()
    mac._validate_bundle(target, manifest['version'])
    result = dict(ok=True, version=manifest['version'], state=state, receipt=receipt,
                  checks=['manifest-size-sha256', 'frozen-helper', 'app-zip-signature-architecture',
                          'wait-for-normal-exit', 'replace-bundle', 'restart-and-acknowledge', 'old-bundle-removed-after-ack'])
    (root / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)
finally:
    if receipt is not None:
        pid = receipt['pid']
        command = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'comm='], capture_output=True, text=True, encoding='utf-8')
        if command.stdout.strip() == str(target / 'Contents/MacOS/Codexio'):
            os.kill(pid, signal.SIGTERM)  # Only our disposable mock app, verified by its exact path.
    cancel_job(directory)
    stop.touch(exist_ok=True)
    parent.wait(timeout=10)
    # Keep only the receipt once all disposable app processes have exited.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        processes = subprocess.check_output(["/bin/ps", "-axo", "comm="], text=True, encoding="utf-8")
        if not any(line.strip().startswith(str(root) + "/") for line in processes.splitlines()):
            shutil.rmtree(root / "install", ignore_errors=True)
            shutil.rmtree(root / "profile", ignore_errors=True)
            break
        time.sleep(0.2)
