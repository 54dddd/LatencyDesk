"""Usage: py -3.12 smoke_exe.py <path-to-exe>; first exit any running LatencyDesk."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import tempfile
import time

exe = Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path(__file__).parent/'dist'/'网络延时助手.exe'
user32 = ctypes.windll.user32
callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

def windows():
    found = {}
    def visit(hwnd, _):
        name = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, name, 512)
        if name.value in ('网络延时助手', '网络波动 · 实时趋势') and user32.IsWindowVisible(hwnd):
            found[name.value] = hwnd
        return True
    user32.EnumWindows(callback(visit), 0)
    return found

with tempfile.TemporaryDirectory(prefix='latency-smoke-') as local:
    root = Path(local)
    codehome = root/'codex'
    codehome.mkdir()
    c = sqlite3.connect(codehome/'logs_2.sqlite')
    c.execute('create table logs (id integer primary key, ts integer, level text, target text, feedback_log_body text)')
    c.execute('insert into logs values (1,?,?,?,?)', (int(time.time()), 'WARN', 'codex_core::responses_retry', 'stream disconnected timeout'))
    c.commit(); c.close()
    config = root/'LatencyDesk'
    config.mkdir()
    config.joinpath('settings.json').write_text(json.dumps({'url':'https://127.0.0.1:9/', 'proxy_url':'https://127.0.0.1:9/',
        'domestic_url':'https://127.0.0.1:9/', 'foreign_url':'https://127.0.0.1:9/', 'proxy':'http://127.0.0.1:9', 'gateway':'127.0.0.1'}), encoding='utf-8')
    env = os.environ.copy()
    env['LOCALAPPDATA'] = local
    env['CODEX_HOME'] = str(codehome)
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    process = subprocess.Popen([str(exe), '--charts'], env=env)
    try:
        deadline = time.monotonic()+25
        while time.monotonic()<deadline:
            found = windows()
            if len(found)==2:
                break
            if process.poll() is not None:
                raise RuntimeError(f'Executable quit: {process.returncode}')
            time.sleep(.25)
        assert len(found)==2, f'Missing windows: {found}'
        second = subprocess.Popen([str(exe)], env=env)
        assert second.wait(timeout=20)==0
        time.sleep(2)
        assert len(windows())==2
        user32.PostMessageW(found['网络波动 · 实时趋势'], 0x0010, 0, 0)
        user32.PostMessageW(found['网络延时助手'], 0x0010, 0, 0)
        assert process.wait(timeout=25)==0
        assert config.joinpath('history.json').is_file()
        data = json.loads(config.joinpath('diagnostics.json').read_text(encoding='utf-8'))
        assert any(e['label']=='请求超时' for e in data['events'])
        assert all(data['records'][k] for k in ('gateway','proxy_port','direct','domestic','foreign','proxy'))
        assert data['records']['gateway'][-1]['ms'] is not None
        assert data['records']['proxy_port'][-1]['ms'] is None
        print('PASS: packaged float/charts, duplicate restore, six collectors, read-only log classification, persistence, clean exit')
    finally:
        if process.poll() is None:
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'], capture_output=True)
