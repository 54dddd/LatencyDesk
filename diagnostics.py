"""Local, read-only network diagnostics. No credentials or raw Codex logs are collected."""
import concurrent.futures
import ctypes
from ctypes import wintypes
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import socket
import sqlite3
import ssl
import subprocess
import time
from urllib.parse import urlsplit

WINDOW = 7200
LAYERS = [('gateway', '本地网关', '#d6c28c'), ('proxy_port', 'Clash 端口', '#b69dd8'),
          ('direct', '国内主目标', '#68dec1'), ('domestic', '国内对照', '#8ec9a7'),
          ('foreign', '代理对照', '#79b9ff'), ('proxy', 'ChatGPT / 自定义', '#c7ddfa')]
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def network_info():
    script = r'''
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$routes = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue
$items = foreach ($r in $routes) {
 $a = Get-NetAdapter -InterfaceIndex $r.InterfaceIndex -ErrorAction SilentlyContinue
 $i = Get-NetIPInterface -InterfaceIndex $r.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
 if ($a.Status -eq 'Up') {
  [pscustomobject]@{gateway=$r.NextHop; name=$r.InterfaceAlias; physical=[bool]$a.HardwareInterface; metric=($r.RouteMetric+$i.InterfaceMetric)}
 }
}
ConvertTo-Json -InputObject @($items) -Compress
'''
    p = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                       capture_output=True, timeout=12, creationflags=NO_WINDOW)
    routes = json.loads(p.stdout.decode('utf-8', errors='replace') or '[]')
    routes = routes if isinstance(routes, list) else [routes]
    physical = sorted([r for r in routes if r.get('physical') and r.get('gateway') != '0.0.0.0'], key=lambda r: r['metric'])
    return {'gateway': physical[0]['gateway'] if physical else '', 'routes': routes}


def ping_gateway(address):
    if not address:
        return {'ms': None, 'detail': '未识别物理网关，请在设置中指定 IPv4 网关', 'kind': 'unavailable'}
    ipaddress.IPv4Address(address)
    class Options(ctypes.Structure):
        _fields_ = [('ttl', ctypes.c_ubyte), ('tos', ctypes.c_ubyte), ('flags', ctypes.c_ubyte),
                    ('size', ctypes.c_ubyte), ('data', ctypes.c_void_p)]
    class Reply(ctypes.Structure):
        _fields_ = [('address', wintypes.ULONG), ('status', wintypes.ULONG), ('rtt', wintypes.ULONG),
                    ('size', wintypes.WORD), ('reserved', wintypes.WORD), ('data', ctypes.c_void_p), ('options', Options)]
    dll = ctypes.WinDLL('iphlpapi.dll')
    dll.IcmpCreateFile.restype = ctypes.c_void_p
    dll.IcmpSendEcho.argtypes = [ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p, wintypes.WORD,
                                ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    dll.IcmpCloseHandle.argtypes = [ctypes.c_void_p]
    handle = dll.IcmpCreateFile()
    if handle in (None, ctypes.c_void_p(-1).value):
        raise OSError('无法创建 ICMP 句柄')
    payload = ctypes.create_string_buffer(b'LatencyDesk')
    buffer = ctypes.create_string_buffer(ctypes.sizeof(Reply) + 128)
    try:
        destination = int.from_bytes(socket.inet_aton(address), 'little')
        count = dll.IcmpSendEcho(handle, destination, payload, 11, None, buffer, len(buffer), 1200)
        reply = ctypes.cast(buffer, ctypes.POINTER(Reply)).contents
        if count and reply.status == 0:
            return {'ms': int(reply.rtt), 'detail': f'ICMP 回应 · {address}', 'kind': 'ok'}
        return {'ms': None, 'detail': '网关未回应 ICMP；也可能是设备禁用 Ping', 'kind': 'timeout'}
    finally:
        dll.IcmpCloseHandle(handle)


def https_probe(url, proxy=None):
    """Fresh CONNECT/TLS/HEAD probe. Does not follow redirects or send authentication."""
    target = urlsplit(url)
    if target.scheme != 'https' or not target.hostname:
        raise ValueError('测试地址必须为 HTTPS')
    connection = None
    started = time.perf_counter()
    stages = {}
    try:
        port = target.port or 443
        if proxy:
            p = urlsplit(proxy)
            connection = http.client.HTTPSConnection(p.hostname, p.port, timeout=4, context=ssl.create_default_context())
            connection.set_tunnel(target.hostname, port)
        else:
            connection = http.client.HTTPSConnection(target.hostname, port, timeout=4, context=ssl.create_default_context())
        connection.connect()
        stages['connect_ms'] = round((time.perf_counter()-started)*1000)
        path = target.path or '/'
        if target.query:
            path += '?' + target.query
        before = time.perf_counter()
        connection.request('HEAD', path, headers={'User-Agent': 'LatencyDesk/2.0', 'Cache-Control': 'no-cache', 'Connection': 'close'})
        response = connection.getresponse()
        stages['response_ms'] = round((time.perf_counter()-before)*1000)
        status = response.status
        # An HTTP refusal proves transport reachability but is not service health.
        return dict(ms=round((time.perf_counter()-started)*1000), status=status,
                    kind='http_error' if status >= 400 else 'ok', detail=f'HTTP {status}', **stages)
    except Exception as e:
        return dict(ms=None, status=None, kind='transport_error', detail=type(e).__name__ + ': ' + str(e)[:180], **stages)
    finally:
        if connection:
            connection.close()


def proxy_port_probe(proxy):
    p = urlsplit(proxy)
    before = time.perf_counter()
    with socket.create_connection((p.hostname, p.port), timeout=2):
        return {'ms': round((time.perf_counter()-before)*1000), 'kind': 'ok', 'detail': '本地代理 TCP 可连接；不代表远端节点正常'}


def classify_event(target, level, body):
    if level not in ('WARN', 'ERROR') or not any(x in target for x in ('responses_retry', 'responses_websocket', 'startup_prewarm')):
        return None
    text = body.lower()
    if not re.search(r'stream disconnected|connection failed|failed.*websocket|websocket.*(?:error|fail)|sampling_error|error=', text):
        return None
    if '10054' in text or 'connection reset' in text:
        return '连接被重置'
    if 'tls' in text or 'certificate' in text:
        return 'TLS 连接异常'
    if 'timed out' in text or 'timeout' in text or '10060' in text:
        return '请求超时'
    if 'decoding response body' in text:
        return '响应流读取失败'
    if 'stream disconnected' in text:
        return '响应流中断'
    return '连接失败 / 重试'


class Diagnostics:
    def __init__(self, directory):
        self.path = Path(directory) / 'diagnostics.json'
        self.records = {key: [] for key, _, _ in LAYERS}
        self.events = []
        self.info = {'gateway': '', 'routes': []}
        self.log_status = '等待检查本机 Codex 日志'
        self.log_cursor = 0
        self.log_source = None
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=7)
        self.queue = queue.Queue()
        self.busy = set()
        self.due = {}
        self.closed = False
        self.save_due = 0
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            for key in self.records:
                self.records[key] = [p for p in data.get('records', {}).get(key, []) if self.valid_record(p)]
            self.events = [e for e in data.get('events', []) if isinstance(e, dict) and isinstance(e.get('ts'), (int, float)) and isinstance(e.get('label'), str)]
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self.prune()

    @staticmethod
    def valid_record(p):
        import math
        return (isinstance(p, dict) and isinstance(p.get('ts'), (int, float)) and math.isfinite(p['ts'])
                and (p.get('ms') is None or isinstance(p['ms'], (int, float)) and math.isfinite(p['ms']) and p['ms'] >= 0)
                and isinstance(p.get('signature'), str))

    def signatures(self, cfg):
        return {'gateway': cfg.get('gateway') or self.info['gateway'], 'proxy_port': cfg['proxy'],
                'direct': cfg['url'], 'domestic': cfg['domestic_url'],
                'foreign': cfg['foreign_url']+'|'+cfg['proxy'], 'proxy': cfg['proxy_url']+'|'+cfg['proxy']}

    def record(self, key, result, signature, stamp=None):
        # Different destinations/routes are never drawn as one continuous series.
        points = self.records[key]
        if points and points[-1]['signature'] != signature:
            points.clear()
        points.append(dict(result, ts=time.time() if stamp is None else stamp, signature=signature))

    def poll_logs(self, path):
        db = Path(path).expanduser()
        if not db.is_file():
            return [], '未找到 Codex 日志；可手动标记卡顿'
        source = str(db.resolve())
        if source != self.log_source:
            self.log_cursor = 0
            self.log_source = source
        c = sqlite3.connect(db.as_uri()+'?mode=ro', uri=True, timeout=.3)
        try:
            maximum = c.execute('select max(id) from logs').fetchone()[0] or 0
            if self.log_cursor > maximum:
                self.log_cursor = 0
            if self.log_cursor == 0:
                # Bounded initial import; later passes only process new IDs.
                self.log_cursor = max(0, maximum-20000)
            rows = c.execute('select id,ts,level,target,feedback_log_body from logs where id>? order by id limit 20000', (self.log_cursor,)).fetchall()
            events = []
            for ident, stamp, level, target, body in rows:
                label = classify_event(target or '', level, body or '')
                if label and time.time()-WINDOW <= stamp <= time.time():
                    digest = hashlib.sha256(f'{source}:{ident}'.encode()).hexdigest()[:20]
                    events.append({'id': digest, 'ts': stamp, 'label': label, 'source': 'Codex'})
            if rows:
                self.log_cursor = rows[-1][0]
            return events, '本机 Codex 日志监控中（仅保存错误分类）'
        finally:
            c.close()

    def submit(self, key, signature, fn, *args):
        self.busy.add(key)
        def work():
            try:
                result = fn(*args)
            except Exception as e:
                result = {'ms': None, 'kind': 'transport_error', 'detail': type(e).__name__ + ': ' + str(e)[:180]}
            self.queue.put((key, signature, result))
        self.pool.submit(work)

    def tick(self, cfg):
        signatures = self.signatures(cfg)
        while True:
            try:
                key, signature, result = self.queue.get_nowait()
            except queue.Empty:
                break
            self.busy.discard(key)
            if key == '_info':
                if 'routes' in result:
                    self.info = result
            elif key == '_logs':
                if signature == cfg['codex_log'] and cfg['watch_codex']:
                    if isinstance(result, tuple):
                        events, self.log_status = result
                        seen = {e.get('id') for e in self.events}
                        self.events.extend(e for e in events if e['id'] not in seen)
                    else:
                        self.log_status = '日志暂不可读；下轮重试'
            elif signature == signatures.get(key):
                self.record(key, result, signature)
        for key, points in self.records.items():
            if points and points[-1]['signature'] != signatures[key]:
                points.clear()
        now = time.monotonic()
        jobs = {'_info': (60, '', network_info, ()),
                'gateway': (10, signatures['gateway'], ping_gateway, (signatures['gateway'],)),
                'proxy_port': (10, signatures['proxy_port'], proxy_port_probe, (cfg['proxy'],)),
                'domestic': (10, signatures['domestic'], https_probe, (cfg['domestic_url'],)),
                'foreign': (10, signatures['foreign'], https_probe, (cfg['foreign_url'], cfg['proxy']))}
        if cfg['watch_codex']:
            jobs['_logs'] = (5, cfg['codex_log'], self.poll_logs, (cfg['codex_log'],))
        else:
            self.log_status = 'Codex 日志监控已关闭'
        for key, (interval, signature, fn, args) in jobs.items():
            if key not in self.busy and now >= self.due.get(key, 0):
                self.due[key] = now+interval
                self.submit(key, signature, fn, *args)
        self.prune()
        if now >= self.save_due:
            self.save_due = now+10
            self.save()

    def mark(self, label):
        stamp = time.time()
        self.events.append({'id': f'manual-{stamp}', 'ts': stamp, 'label': label[:80], 'source': '手动'})
        self.save()

    def prune(self):
        now = time.time()
        for key in self.records:
            self.records[key] = [p for p in self.records[key] if now-WINDOW <= p['ts'] <= now]
        self.events = sorted([e for e in self.events if now-WINDOW <= e['ts'] <= now], key=lambda e: e['ts'])[-3000:]

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps({'records': self.records, 'events': self.events}, ensure_ascii=False), encoding='utf-8')
            temp.replace(self.path)
        except OSError:
            pass

    def summary(self):
        def failed(key):
            points = [p for p in self.records[key] if p['ts'] >= time.time()-45][-3:]
            return len(points) >= 3 and all(p['ms'] is None for p in points)
        def healthy(key):
            points = [p for p in self.records[key] if p['ts'] >= time.time()-30][-3:]
            return len(points) >= 2 and all(p['ms'] is not None and p.get('kind') != 'http_error' for p in points)
        if failed('proxy_port'):
            return '线索：代理端口连续不可连接，先检查 Clash 是否运行及端口设置。'
        if failed('gateway'):
            return '线索：网关连续未回应。先核实是否禁用 Ping，再检查本机到路由器。'
        if failed('direct') and failed('domestic'):
            return '线索：两个国内目标均连接失败，检查本地网络、出口或路由接管。'
        if healthy('direct') and healthy('domestic') and failed('foreign') and failed('proxy'):
            return '线索：国内正常、两路代理目标失败，优先对照代理节点及境外路径。'
        if any(e['source'] == 'Codex' and e['ts'] >= time.time()-60 for e in self.events):
            return '线索：最近出现 Codex 连接事件，请对齐曲线；短请求正常也不能排除断流。'
        return '正在积累对照数据。提示只用于缩小排查范围，不能自动判定责任。'

    def close(self):
        self.closed = True
        self.save()
        self.pool.shutdown(wait=False, cancel_futures=True)
