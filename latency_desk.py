import concurrent.futures
import ctypes
import json
import math
import os
from pathlib import Path
import queue
import socket
import time
import tkinter as tk
from tkinter import ttk, messagebox
from urllib.parse import urlsplit
import winreg
from diagnostics import Diagnostics, LAYERS, https_probe


APP = Path(__file__).resolve().parent
CONFIG = Path(os.environ.get('LOCALAPPDATA', str(APP))) / 'LatencyDesk' / 'settings.json'
HISTORY = CONFIG.with_name('history.json')
WINDOW_SECONDS = 2 * 60 * 60


def discover_proxy():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
            server = winreg.QueryValueEx(key, 'ProxyServer')[0]
            if '=' in server:
                pairs = dict(x.split('=', 1) for x in server.split(';') if '=' in x)
                server = pairs.get('https', pairs.get('http', ''))
            if server:
                return server if '://' in server else 'http://' + server
    except OSError:
        pass
    for port in (7890, 7897, 10809):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.15):
                return f'http://127.0.0.1:{port}'
        except OSError:
            pass
    return 'http://127.0.0.1:7890'


def measure(url, proxy):
    return https_probe(url, proxy)


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('网络延时助手')
        self.root.configure(bg='#142031')
        self.root.resizable(False, False)
        self.root.attributes('-topmost', True)
        self.root.overrideredirect(True)
        if (APP / 'latency.ico').exists():
            self.root.iconbitmap(str(APP / 'latency.ico'))
        self.cfg = {'url': 'https://www.baidu.com/', 'proxy_url': 'https://chatgpt.com/', 'proxy': discover_proxy(), 'interval': 5,
                    'x': 80, 'y': 100, 'topmost': True, 'opacity': 0.88,
                    'domestic_url': 'https://www.qq.com/', 'foreign_url': 'https://www.gstatic.com/generate_204',
                    'gateway': '', 'codex_log': str(Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex'))) / 'logs_2.sqlite'), 'watch_codex': True}
        try:
            self.cfg.update(json.loads(CONFIG.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            pass
        self.root.geometry(f"320x204+{max(0, int(self.cfg['x']))}+{max(0, int(self.cfg['y']))}")
        self.root.attributes('-topmost', self.cfg['topmost'])
        self.root.attributes('-alpha', self.cfg['opacity'])
        self.results = queue.Queue()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.running = False
        self.closed = False
        self.generation = 0
        self.pending = 0
        self.next_run = 0
        self.stats = {'direct': [], 'proxy': []}
        self.samples = {'direct': [], 'proxy': []}
        self.chart_window = None
        self.chart_refresh = 0
        self.load_history()
        self.diag = Diagnostics(CONFIG.parent)
        self.details = {}
        title = tk.Frame(self.root, bg='#142031')
        title.pack(fill='x')
        label = tk.Label(title, text='●  NET / 网络状态', bg='#142031', fg='#b9c9dc', font=('Microsoft YaHei UI', 9), pady=7)
        label.pack(side='left', padx=12)
        for widget in (title, label):
            widget.bind('<ButtonPress-1>', self.drag_start)
            widget.bind('<B1-Motion>', self.drag)
            widget.bind('<ButtonRelease-1>', lambda e: self.save())
        tk.Button(title, text='×', command=self.close, bg='#142031', fg='#879ab1', bd=0, font=('Segoe UI', 12)).pack(side='right', padx=9)
        tk.Button(title, text='⚙', command=self.settings, bg='#142031', fg='#879ab1', bd=0).pack(side='right')
        self.values = {}
        self.sparks = {}
        for key, name in [('direct', '直连'), ('proxy', 'Clash 代理')]:
            row = tk.Frame(self.root, bg='#142031')
            row.pack(fill='x', padx=16, pady=(3, 0))
            tk.Label(row, text=name, bg='#142031', fg='#a8b9cf', font=('Microsoft YaHei UI', 10)).pack(side='left')
            value = tk.Label(row, text='···', bg='#142031', fg='#68dec1' if key == 'direct' else '#79b9ff', font=('Bahnschrift', 19))
            value.pack(side='right')
            self.values[key] = value
            spark = tk.Canvas(self.root, height=26, bg='#142031', highlightthickness=0)
            spark.pack(fill='x', padx=16, pady=(0, 3))
            self.sparks[key] = spark
        self.status = tk.Label(self.root, text='正在连接…', bg='#142031', fg='#91a3bb', font=('Microsoft YaHei UI', 8))
        self.status.pack(pady=(4, 0))
        tk.Button(self.root, text='实时波动  /  展开趋势  ↗', command=self.show_charts,
                  bg='#142031', fg='#a9bcd1', bd=0, font=('Microsoft YaHei UI', 8)).pack(fill='x', padx=16, pady=(0, 4))
        self.root.geometry('286x236')
        self.root.after(50, self.restore_float)
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label='设置 / 测试地址 / 代理端口', command=self.settings)
        menu.add_command(label='立即刷新', command=lambda: setattr(self, 'next_run', 0))
        menu.add_command(label='查看测试详情', command=self.show_details)
        menu.add_command(label='近两小时折线图', command=self.show_charts)
        menu.add_command(label='退出', command=self.close)
        self.root.bind('<Button-3>', lambda e: menu.tk_popup(e.x_root, e.y_root))
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(100, self.tick)

    def drag_start(self, event):
        self.offset = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def drag(self, event):
        self.root.geometry(f'+{max(0, event.x_root - self.offset[0])}+{max(0, event.y_root - self.offset[1])}')

    def restore_float(self):
        self.root.update_idletasks()
        from ctypes import wintypes
        area = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(area), 0)
        x = min(max(area.left, int(self.cfg['x'])), max(area.left, area.right - 286))
        y = min(max(area.top, int(self.cfg['y'])), max(area.top, area.bottom - 236))
        self.root.geometry(f'+{x}+{y}')
        self.root.deiconify()
        self.root.attributes('-topmost', True)
        self.root.lift()
        self.root.after(1200, lambda: self.root.attributes('-topmost', self.cfg['topmost']))

    def draw_sparks(self):
        now = time.time()
        start, end = self.time_range(now)
        span = end - start
        values = [p[1] for points in self.samples.values() for p in points if p[1] is not None]
        ceiling = max(values, default=100) or 100
        for key, color in [('direct', '#68dec1'), ('proxy', '#79b9ff')]:
            c = self.sparks[key]
            c.delete('all')
            width = max(1, c.winfo_width())
            c.create_line(0, 25, width, 25, fill='#28384b')
            previous = None
            for stamp, value, interval in self.samples[key]:
                x = (stamp-start)/span*width
                if value is None:
                    c.create_line(x, 22, x, 25, fill='#fb8c96')
                    previous = None
                    continue
                y = 22 - value/ceiling*19
                if previous and stamp-previous[0] <= max(interval, previous[3])*2+10:
                    c.create_line(previous[1], previous[2], x, y, fill=color, width=1.5)
                previous = (stamp, x, y, interval)
            if previous:
                c.create_oval(previous[1]-2, previous[2]-2, previous[1]+2, previous[2]+2, fill=color, outline='')

    def time_range(self, now=None):
        now = time.time() if now is None else now
        timestamps = [p[0] for points in self.samples.values() for p in points
                      if now-WINDOW_SECONDS <= p[0] <= now]
        # Fill the available width from the first retained observation to now.
        # A one-second minimum keeps a lone first sample well-defined.
        start = min(timestamps, default=now)
        return max(now-WINDOW_SECONDS, min(start, now-1)), now

    def save(self):
        self.cfg.update(x=self.root.winfo_x(), y=self.root.winfo_y())
        try:
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            CONFIG.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError:
            pass

    def tick(self):
        if getattr(self, 'restore_event', None) and ctypes.windll.kernel32.WaitForSingleObject(self.restore_event, 0) == 0:
            self.restore_float()
        while True:
            try:
                gen, key, result = self.results.get_nowait()
            except queue.Empty:
                break
            if gen != self.generation:
                continue
            value, detail = result['ms'], result['detail']
            self.diag.record(key, result, self.diag.signatures(self.cfg)[key])
            if 'connect_ms' in result:
                detail += f" · 建连 {result['connect_ms']} ms"
            if 'response_ms' in result:
                detail += f" · 响应头 {result['response_ms']} ms"
            self.details[key] = detail
            self.samples[key].append([time.time(), value, self.cfg['interval']])
            self.prune_history()
            history = self.stats[key]
            history.append(value)
            del history[:-60]
            warning = result.get('kind') == 'http_error'
            self.values[key].configure(text=(f'{value} ms' + (' !' if warning else '')) if value is not None else '连接失败',
                fg='#f6c76c' if warning else '#68dec1' if value is not None and value < 500 else '#f6c76c' if value is not None else '#fb8c96')
            self.pending -= 1
            if self.pending == 0:
                self.save_history()
                self.running = False
                self.next_run = time.monotonic() + self.cfg['interval']
                self.status.configure(text=f"{time.strftime('%H:%M:%S')} 更新 · 每 {self.cfg['interval']} 秒测试")
        if not self.running and time.monotonic() >= self.next_run:
            self.running = True
            self.pending = 2
            self.status.configure(text='正在测试…')
            gen = self.generation
            for key, proxy in [('direct', None), ('proxy', self.cfg['proxy'])]:
                url = self.cfg['url'] if key == 'direct' else self.cfg['proxy_url']
                future = self.pool.submit(measure, url, proxy)
                future.add_done_callback(lambda f, k=key, g=gen: None if f.cancelled() else self.results.put((g, k, f.result())))
        self.diag.tick(self.cfg)
        if time.monotonic() >= self.chart_refresh:
            self.prune_history()
            self.draw_sparks()
            self.draw_charts()
            self.draw_diagnostics()
            self.chart_refresh = time.monotonic() + 1
        if not self.closed:
            self.root.after(150, self.tick)

    def prune_history(self):
        cutoff = time.time() - WINDOW_SECONDS
        for key in self.samples:
            self.samples[key] = [p for p in self.samples[key] if cutoff <= p[0] <= time.time()]

    def load_history(self):
        try:
            data = json.loads(HISTORY.read_text(encoding='utf-8'))
            for key in self.samples:
                if key == 'direct' and data.get('url') != self.cfg['url']:
                    continue
                if key == 'proxy' and (data.get('proxy_url', data.get('url')) != self.cfg['proxy_url']
                                       or data.get('proxy') != self.cfg['proxy']):
                    continue
                self.samples[key] = [p for p in data['samples'][key]
                    if isinstance(p, list) and len(p) == 3
                    and isinstance(p[0], (int, float)) and math.isfinite(p[0])
                    and (p[1] is None or isinstance(p[1], (int, float)) and math.isfinite(p[1]) and p[1] >= 0)
                    and isinstance(p[2], (int, float)) and 2 <= p[2] <= 300]
            self.prune_history()
        except (OSError, ValueError, KeyError, TypeError):
            self.samples = {'direct': [], 'proxy': []}

    def save_history(self):
        self.prune_history()
        try:
            HISTORY.parent.mkdir(parents=True, exist_ok=True)
            temporary = HISTORY.with_suffix('.tmp')
            temporary.write_text(json.dumps({'url': self.cfg['url'], 'proxy_url': self.cfg['proxy_url'], 'proxy': self.cfg['proxy'],
                                             'samples': self.samples}), encoding='utf-8')
            temporary.replace(HISTORY)
        except OSError:
            pass

    def show_charts(self):
        if self.chart_window and self.chart_window.winfo_exists():
            self.chart_window.deiconify()
            self.chart_window.lift()
            return
        win = self.chart_window = tk.Toplevel(self.root)
        win.title('网络波动 · 实时趋势')
        from ctypes import wintypes
        area = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(area), 0)
        beside = self.root.winfo_x()+300
        if beside+520 > area.right:
            beside = self.root.winfo_x()-534
        x = max(area.left, min(beside, area.right-520))
        y = max(area.top, min(self.root.winfo_y(), area.bottom-440))
        win.geometry(f'520x440+{x}+{y}')
        win.overrideredirect(True)
        win.resizable(False, False)
        win.attributes('-topmost', self.cfg['topmost'])
        win.attributes('-alpha', self.cfg['opacity'])
        win.configure(bg='#142031')
        header = tk.Frame(win, bg='#142031')
        header.pack(fill='x', padx=16, pady=(9, 0))
        heading = self.chart_heading = tk.Label(header, text='实时趋势', bg='#142031', fg='#e8f0fb', font=('Microsoft YaHei UI', 10, 'bold'))
        heading.pack(side='left')
        def start_drag(e):
            win.drag_offset = (e.x_root-win.winfo_x(), e.y_root-win.winfo_y())
        def move(e):
            win.geometry(f'+{max(0,e.x_root-win.drag_offset[0])}+{max(0,e.y_root-win.drag_offset[1])}')
        for widget in (header, heading):
            widget.bind('<ButtonPress-1>', start_drag)
            widget.bind('<B1-Motion>', move)
        def dismiss():
            win.destroy()
            self.restore_float()
        tk.Button(header, text='×', command=dismiss, bg='#142031', fg='#879ab1', bd=0).pack(side='right')
        tk.Button(header, text='返回浮窗 ↗', command=self.restore_float, bg='#142031', fg='#a9bcd1', bd=0, font=('Microsoft YaHei UI', 8)).pack(side='right', padx=8)
        win.bind('<Escape>', lambda e: dismiss())
        win.protocol('WM_DELETE_WINDOW', dismiss)
        self.chart_target = tk.Label(win, bg='#142031', fg='#91a3bb', font=('Microsoft YaHei UI', 9))
        self.chart_target.pack(anchor='w', padx=18, pady=(3, 0))
        style = ttk.Style(win)
        style.theme_use('clam')
        style.configure('Latency.TNotebook', background='#142031', borderwidth=0, bordercolor='#142031', lightcolor='#142031', darkcolor='#142031')
        style.configure('Latency.TNotebook.Tab', background='#1c2c40', foreground='#93a8c1', padding=(12, 6), borderwidth=0,
                        bordercolor='#142031', lightcolor='#142031', darkcolor='#142031', focuscolor='#294159', font=('Microsoft YaHei UI', 9))
        style.map('Latency.TNotebook.Tab', background=[('selected', '#294159')], foreground=[('selected', '#e4edf9')])
        tabs = ttk.Notebook(win, style='Latency.TNotebook')
        tabs.pack(fill='both', expand=True, padx=10, pady=(8, 0))
        access = tk.Frame(tabs, bg='#142031')
        layers = tk.Frame(tabs, bg='#142031')
        events = tk.Frame(tabs, bg='#142031')
        tabs.add(access, text='访问趋势')
        tabs.add(layers, text='分层排查')
        tabs.add(events, text='Codex 事件')
        self.canvases = {}
        for key in ('direct', 'proxy'):
            canvas = tk.Canvas(access, height=128, bg='#142031', highlightthickness=0)
            canvas.pack(fill='both', expand=True, padx=10, pady=2)
            canvas.bind('<Configure>', lambda e: self.draw_charts())
            self.canvases[key] = canvas
        self.layer_canvas = tk.Canvas(layers, height=204, bg='#142031', highlightthickness=0)
        self.layer_canvas.pack(fill='both', expand=True)
        self.layer_canvas.bind('<Configure>', lambda e: self.draw_diagnostics())
        self.layer_canvas.bind('<Motion>', self.layer_hover)
        self.layer_canvas.bind('<Leave>', lambda e: self.layer_hint.configure(text='悬停查看目标与错误。网关未响应也可能是设备禁用 ICMP。'))
        self.layer_hint = tk.Label(layers, text='悬停查看目标与错误。网关未响应也可能是设备禁用 ICMP。',
                                   bg='#142031', fg='#91a3bb', font=('Microsoft YaHei UI', 8), wraplength=475, justify='left', anchor='w')
        self.layer_hint.pack(fill='x', padx=10, pady=4)
        self.diagnosis_label = tk.Label(layers, bg='#142031', fg='#d3c69c', font=('Microsoft YaHei UI', 8), wraplength=475, justify='left', anchor='w')
        self.diagnosis_label.pack(fill='x', padx=10, pady=(0, 4))
        bar = tk.Frame(layers, bg='#142031')
        bar.pack(fill='x', padx=8)
        self.small_button(bar, '排障设置', self.diagnostic_settings).pack(side='left')
        self.small_button(bar, '标记卡顿 / 切换', self.mark_event).pack(side='left', padx=6)
        self.small_button(bar, '导出记录', self.export_diagnostics).pack(side='right')
        self.log_label = tk.Label(events, bg='#142031', fg='#91a3bb', font=('Microsoft YaHei UI', 8), wraplength=475, anchor='w')
        self.log_label.pack(fill='x', padx=8, pady=8)
        style.configure('Latency.Treeview', background='#182638', fieldbackground='#182638', foreground='#bfcddd', rowheight=23,
                        font=('Microsoft YaHei UI', 8), borderwidth=0, bordercolor='#182638', lightcolor='#182638', darkcolor='#182638')
        style.configure('Latency.Treeview.Heading', background='#22364c', foreground='#d4e1ef', borderwidth=0,
                        bordercolor='#22364c', lightcolor='#22364c', darkcolor='#22364c', font=('Microsoft YaHei UI', 8))
        self.event_tree = ttk.Treeview(events, columns=('time', 'source', 'event'), show='headings', height=8, style='Latency.Treeview')
        self._event_version = None
        for col, text, width in [('time', '时间', 75), ('source', '来源', 60), ('event', '事件（仅分类）', 320)]:
            self.event_tree.heading(col, text=text)
            self.event_tree.column(col, width=width, minwidth=40, stretch=col=='event')
        scroll = ttk.Scrollbar(events, orient='vertical', command=self.event_tree.yview)
        self.event_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y', padx=(0, 5))
        self.event_tree.pack(fill='both', expand=True, padx=(8, 0))
        event_bar = tk.Frame(events, bg='#142031')
        event_bar.pack(fill='x', padx=8, pady=5)
        self.small_button(event_bar, '标记当前卡顿', self.mark_event).pack(side='left')
        self.small_button(event_bar, '导出记录', self.export_diagnostics).pack(side='right')
        tk.Label(win, text='动态时间轴 ≤ 2 小时   ·   紫线：Codex / 手动事件   ·   !：HTTP 错误',
                 bg='#142031', fg='#73869f', font=('Microsoft YaHei UI', 8)).pack(pady=(0, 9))
        self.chart_refresh = 0
        self.draw_charts()
        self.draw_diagnostics()

    def draw_charts(self):
        if not self.chart_window or not self.chart_window.winfo_exists():
            return
        self.prune_history()
        start, now = self.time_range()
        span = now - start
        minutes, seconds = divmod(int(span), 60)
        duration = f'{minutes} 分 {seconds:02d} 秒' if minutes else f'{seconds} 秒'
        self.chart_heading.configure(text=f'实时趋势  /  {duration}')
        self.chart_target.configure(text=f"直连 {urlsplit(self.cfg['url']).hostname}   /   代理 {urlsplit(self.cfg['proxy_url']).hostname}", font=('Microsoft YaHei UI', 8))
        values = [p[1] for points in self.samples.values() for p in points if p[1] is not None]
        maximum = max(values, default=0)
        ceiling = max(100, math.ceil(maximum * 1.12 / 50) * 50)
        for key, name, color in [('direct', '直连', '#68dec1'), ('proxy', 'Clash 代理', '#79b9ff')]:
            c = self.canvases[key]
            c.delete('all')
            width, height = max(c.winfo_width(), 440), max(c.winfo_height(), 130)
            left, right, top, bottom = 42, width - 15, 43, height - 22
            points = self.samples[key]
            good = [p[1] for p in points if p[1] is not None]
            summary = (f"均值 {round(sum(good)/len(good))} ms   ·   峰值 {max(good)}" if good else '等待采样')
            summary += f"  ·  失败 {len(points)-len(good)} 次"
            c.create_oval(left-13, 9, left-7, 15, fill=color, outline='')
            c.create_text(left, 12, text=name, anchor='w', fill=color, font=('Microsoft YaHei UI', 9, 'bold'))
            c.create_text(left, 31, text=summary, anchor='w', fill='#91a3bb', font=('Microsoft YaHei UI', 8))
            latest = points[-1][1] if points else None
            c.create_text(right, 15, text=f'{latest} ms' if latest is not None else '—', anchor='e', fill='#dce7f5', font=('Bahnschrift', 17))
            for i in range(3):
                y = bottom - (bottom-top)*i/2
                c.create_line(left, y, right, y, fill='#223246', dash=(2, 5))
                c.create_text(left-7, y, text=f'{ceiling*i/2:g}', anchor='e', fill='#73869f', font=('Segoe UI', 8))
            for i in range(3):
                x = left + (right-left)*i/2
                c.create_text(x, bottom+13, text=time.strftime('%H:%M:%S' if span < 180 else '%H:%M', time.localtime(start+span*i/2)),
                              anchor='w' if i == 0 else 'e' if i == 2 else 'center', fill='#73869f', font=('Segoe UI', 8))
            previous = None
            segment = []
            def paint_segment():
                if len(segment) >= 4:
                    c.create_polygon(segment[0], bottom, *segment, segment[-2], bottom,
                                     fill='#193537' if key == 'direct' else '#1b2e45', outline='')
                    c.create_line(*segment, fill=color, width=2, joinstyle='round', capstyle='round')
                segment.clear()
            for stamp, value, interval in points:
                x = left + (stamp-start)/span*(right-left)
                if value is None:
                    paint_segment()
                    c.create_oval(x-1.5, bottom-3, x+1.5, bottom, fill='#fb8c96', outline='')
                    previous = None
                    continue
                y = bottom - value/ceiling*(bottom-top)
                if previous and stamp-previous[0] > max(interval, previous[3])*2+10:
                    paint_segment()
                segment.extend((x, y))
                previous = (stamp, x, y, interval)
            paint_segment()
            for event in self.diag.events:
                if start <= event['ts'] <= now:
                    x = left+(event['ts']-start)/span*(right-left)
                    c.create_line(x, top, x, bottom, fill='#ad7fae', dash=(2, 4))
            if previous:
                c.create_oval(previous[1]-2.5, previous[2]-2.5, previous[1]+2.5, previous[2]+2.5, fill=color, outline='')
            if not points:
                c.create_text((left+right)/2, (top+bottom)/2, text='暂无记录，采样后自动绘制', fill='#91a3bb', font=('Microsoft YaHei UI', 11))

    @staticmethod
    def small_button(parent, text, command):
        return tk.Button(parent, text=text, command=command, bg='#21344b', fg='#b8cadf',
                         activebackground='#2c455f', activeforeground='#edf5ff', bd=0, padx=9, pady=4,
                         font=('Microsoft YaHei UI', 8))

    def layer_hover(self, event):
        canvas = self.layer_canvas
        row_height = max(26, (canvas.winfo_height()-20)/6)
        index = int(event.y/row_height)
        if 0 <= index < len(LAYERS):
            key, name, _ = LAYERS[index]
            points = self.diag.records[key]
            latest = points[-1] if points else None
            target = self.diag.signatures(self.cfg)[key].split('|')[0]
            detail = latest.get('detail', '') if latest else '等待采样'
            stages = ''
            if latest and 'connect_ms' in latest:
                stages = f" · 建连(含DNS/TCP/TLS/代理隧道) {latest['connect_ms']} ms · 响应头 {latest.get('response_ms', '—')} ms"
            if points:
                good = sorted(p['ms'] for p in points if p['ms'] is not None)
                if good:
                    stages += f" · 均值 {round(sum(good)/len(good))} ms · P95 {good[max(0, math.ceil(len(good)*.95)-1)]} ms"
            self.layer_hint.configure(text=f'{name}：{target}\n{detail}{stages}')

    def draw_diagnostics(self):
        if not self.chart_window or not self.chart_window.winfo_exists() or not hasattr(self, 'layer_canvas'):
            return
        c = self.layer_canvas
        c.delete('all')
        width, height = max(c.winfo_width(), 475), max(c.winfo_height(), 200)
        now = time.time()
        stamps = [p['ts'] for points in self.diag.records.values() for p in points]
        start = max(now-WINDOW_SECONDS, min(stamps, default=now-1))
        span = max(1, now-start)
        left, right = 158, width-102
        row_height = (height-20)/6
        for i, (key, name, color) in enumerate(LAYERS):
            y = i*row_height
            points = self.diag.records[key]
            good = [p['ms'] for p in points if p['ms'] is not None]
            latest = points[-1] if points else None
            c.create_text(9, y+10, text=name, anchor='w', fill=color, font=('Microsoft YaHei UI', 9))
            if points:
                failures = sum(p['ms'] is None for p in points)
                refusals = sum(p.get('kind') == 'http_error' for p in points)
                sub = f'连接失败 {failures}/{len(points)}' + (f' · HTTP拒绝 {refusals}' if refusals else '')
            else:
                sub = '等待采样'
            c.create_text(9, y+25, text=sub, anchor='w', fill='#70869f', font=('Microsoft YaHei UI', 7))
            base, top = y+27, y+5
            c.create_line(left, base, right, base, fill='#26384b')
            ceiling = max(good, default=100) or 1
            previous = None
            for p in points:
                x = left+(p['ts']-start)/span*(right-left)
                if p['ms'] is None:
                    c.create_oval(x-1, base-2, x+1, base, fill='#f28e98', outline='')
                    previous = None
                    continue
                py = base-3-p['ms']/ceiling*(base-top-5)
                if previous and p['ts']-previous[0] <= max(30, self.cfg['interval']*2+10):
                    c.create_line(previous[1], previous[2], x, py, fill=color, width=1.5)
                previous = (p['ts'], x, py)
            for e in self.diag.events:
                if start <= e['ts'] <= now:
                    x = left+(e['ts']-start)/span*(right-left)
                    c.create_line(x, top, x, base, fill='#ad7fae', dash=(2, 4))
            text, ink = '—', '#91a3bb'
            if latest:
                if latest['ms'] is None:
                    text, ink = ('未识别' if latest.get('kind') == 'unavailable' else '未响应'), '#f28e98'
                elif latest.get('kind') == 'http_error':
                    text, ink = f"HTTP {latest.get('status')}", '#f6c76c'
                else:
                    text, ink = f"{latest['ms']} ms", color
            c.create_text(width-8, y+15, text=text, anchor='e', fill=ink, font=('Bahnschrift', 12))
        for i in range(3):
            x = left+(right-left)*i/2
            c.create_text(x, height-7, text=time.strftime('%H:%M:%S' if span<180 else '%H:%M', time.localtime(start+span*i/2)),
                          fill='#70869f', font=('Segoe UI', 7), anchor='w' if i==0 else 'e' if i==2 else 'center')
        routes = self.diag.info.get('routes', [])
        virtual = [r.get('name', '') for r in routes if not r.get('physical')]
        suffix = ' · 检测到虚拟默认路由，请核实直连是否被接管' if virtual else ''
        self.diagnosis_label.configure(text=self.diag.summary()+(' 虚拟默认路由存在，直连可能被接管。' if virtual else ''))
        self.log_label.configure(text=self.diag.log_status+suffix)
        event_version = tuple(e.get('id') for e in self.diag.events)
        if event_version != getattr(self, '_event_version', None):
            self._event_version = event_version
            self.event_tree.delete(*self.event_tree.get_children())
            for event in reversed(self.diag.events):
                self.event_tree.insert('', 'end', values=(time.strftime('%H:%M:%S', time.localtime(event['ts'])), event['source'], event['label']))

    def mark_event(self):
        from tkinter import simpledialog
        label = simpledialog.askstring('标记观察事件', '记录当前卡顿、切换节点或更换网络：',
                                       initialvalue='当前卡顿', parent=self.chart_window or self.root)
        if label and label.strip():
            self.diag.mark(label.strip())
            self.chart_refresh = 0

    def export_diagnostics(self):
        import csv
        import io
        import zipfile
        from tkinter import filedialog
        target = filedialog.asksaveasfilename(parent=self.chart_window, title='导出最近两小时的诊断记录',
                                             initialfile=f"网络诊断-{time.strftime('%Y%m%d-%H%M%S')}.zip", defaultextension='.zip', filetypes=[('ZIP', '*.zip')])
        if not target:
            return
        try:
            self.diag.prune()
            samples = io.StringIO(newline='')
            writer = csv.writer(samples)
            writer.writerow(['时间(本机时区)', '监测层', '延迟ms', 'HTTP状态', '结果分类', '建连ms', '响应头ms'])
            for key, name, _ in LAYERS:
                for p in self.diag.records[key]:
                    writer.writerow([time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(p['ts'])), name,
                                     p['ms'], p.get('status', ''), p.get('kind', ''), p.get('connect_ms', ''), p.get('response_ms', '')])
            events = io.StringIO(newline='')
            writer = csv.writer(events)
            writer.writerow(['时间(本机时区)', '来源', '事件分类/手动备注'])
            for e in self.diag.events:
                label = e['label']
                if label.startswith(('=', '+', '-', '@')):
                    label = "'"+label
                writer.writerow([time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(e['ts'])), e['source'], label])
            context = {'version': '2.0', 'timezone': time.tzname, 'gateway': self.cfg['gateway'] or self.diag.info['gateway'],
                       'targets': {k: urlsplit(self.cfg[v]).hostname for k, v in [('direct', 'url'), ('domestic', 'domestic_url'), ('foreign', 'foreign_url'), ('proxy', 'proxy_url')]},
                       'proxy_host': urlsplit(self.cfg['proxy']).hostname, 'proxy_port': urlsplit(self.cfg['proxy']).port,
                       'routes': self.diag.info.get('routes', []), 'log_monitor': self.diag.log_status}
            with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('samples.csv', samples.getvalue().encode('utf-8-sig'))
                archive.writestr('events.csv', events.getvalue().encode('utf-8-sig'))
                archive.writestr('environment.json', json.dumps(context, ensure_ascii=False, indent=2))
            messagebox.showinfo('导出完成', '已导出采样、事件分类和网络环境。包含本机网关/网卡信息及手动备注，请确认后再分享。', parent=self.chart_window)
        except (OSError, ValueError) as e:
            messagebox.showerror('导出失败', str(e), parent=self.chart_window)

    def diagnostic_settings(self):
        if getattr(self, 'diag_dialog', None) and self.diag_dialog.winfo_exists():
            self.diag_dialog.lift()
            return
        win = self.diag_dialog = tk.Toplevel(self.root)
        win.title('分层排障设置')
        win.attributes('-topmost', True)
        frame = ttk.Frame(win, padding=16)
        frame.pack()
        fields = [('gateway', '物理网关 IPv4（留空自动检测）'), ('domestic_url', '国内对照（不使用 HTTP 代理）'),
                  ('foreign_url', '境外对照（使用同一 Clash 端口）'), ('codex_log', 'Codex 本机日志数据库路径（只读）')]
        variables = {}
        for i, (key, label) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=i*2, column=0, sticky='w', pady=(7, 2))
            variables[key] = tk.StringVar(value=self.cfg[key])
            ttk.Entry(frame, textvariable=variables[key], width=57).grid(row=i*2+1, column=0)
        watch = tk.BooleanVar(value=self.cfg['watch_codex'])
        ttk.Checkbutton(frame, text='读取本机 Codex 错误事件（仅时间和分类）', variable=watch).grid(row=8, column=0, sticky='w', pady=8)
        ttk.Label(frame, text='网关/端口/对照目标约每 10 秒测试，日志约每 5 秒检查。\n日志结构不兼容时显示不可读，可用手动标记替代。\n短请求监测不模拟 Codex 登录，也不验证真实响应流。\n不会调用付费模型；不读取 API 密钥或改动代理规则。', foreground='#666666').grid(row=9, column=0, sticky='w')
        def apply():
            import ipaddress
            values = {k: v.get().strip() for k, v in variables.items()}
            try:
                if values['gateway']:
                    ipaddress.IPv4Address(values['gateway'])
                for key in ('domestic_url', 'foreign_url'):
                    p = urlsplit(values[key])
                    if p.scheme != 'https' or not p.hostname or p.username or p.password:
                        raise ValueError('对照地址应为不含登录信息的 HTTPS URL')
                if watch.get() and not values['codex_log']:
                    raise ValueError('请填写日志数据库路径，或关闭日志读取')
            except ValueError as e:
                messagebox.showerror('设置无效', str(e), parent=win)
                return
            self.cfg.update(values, watch_codex=watch.get())
            self.save()
            self.diag.due.clear()
            self.chart_refresh = 0
            win.destroy()
        ttk.Button(frame, text='保存', command=apply).grid(row=10, column=0, sticky='e', pady=(12, 0))

    def show_details(self):
        lines = [f"直连地址：{self.cfg['url']}", f"代理测试地址：{self.cfg['proxy_url']}", f"代理端口：{self.cfg['proxy']}", '']
        for key, name in [('direct', '直连'), ('proxy', 'Clash 代理')]:
            history = self.stats[key]
            good = [x for x in history if x is not None]
            lines.append(f"{name}：{self.details.get(key, '等待测试')}")
            if history:
                lines.append(f"最近 {len(history)} 次：失败 {history.count(None)} 次" + (f'，平均 {round(sum(good)/len(good))} ms' if good else ''))
        lines += ['', '耗时包含 DNS、TCP、TLS 和服务器响应，不是 ICMP Ping。',
                  'HTTP 403 / 429 等状态也记录响应耗时，不代表服务可正常使用。',
                  '两路目标不同时，用于分别看访问体验，不代表代理额外开销。',
                  '代理请求遵循 Clash 分流规则，不保证经过远端节点。', 'TUN / VPN 可能接管直连流量。']
        messagebox.showinfo('测试详情', '\n'.join(lines), parent=self.root)

    def settings(self):
        if getattr(self, 'dialog', None) and self.dialog.winfo_exists():
            self.dialog.lift()
            return
        win = self.dialog = tk.Toplevel(self.root)
        win.title('网络延时助手设置')
        win.attributes('-topmost', True)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=18)
        frame.pack(fill='both', expand=True)
        entries = {}
        for row, (key, label) in enumerate([('url', '直连测试地址（默认百度）'), ('proxy_url', 'Clash 测试地址（默认 ChatGPT）'), ('proxy', 'Clash HTTP / Mixed 代理'), ('interval', '完成后等待（秒，2–300）')]):
            ttk.Label(frame, text=label).grid(row=row*2, column=0, sticky='w', pady=(8, 2))
            var = tk.StringVar(value=str(self.cfg[key]))
            ttk.Entry(frame, textvariable=var, width=49).grid(row=row*2+1, column=0, sticky='ew')
            entries[key] = var
        ttk.Button(frame, text='自动检测本机代理', command=lambda: entries['proxy'].set(discover_proxy())).grid(row=8, column=0, sticky='w', pady=8)
        top = tk.BooleanVar(value=self.cfg['topmost'])
        ttk.Checkbutton(frame, text='悬浮窗始终置顶', variable=top).grid(row=9, column=0, sticky='w')
        opacity = tk.DoubleVar(value=self.cfg['opacity'] * 100)
        opacity_row = ttk.Frame(frame)
        opacity_row.grid(row=10, column=0, sticky='ew', pady=(9, 0))
        ttk.Label(opacity_row, text='不透明度').pack(side='left')
        opacity_label = ttk.Label(opacity_row, text=f"{round(opacity.get())}%")
        opacity_label.pack(side='right')
        def preview_alpha(value):
            alpha = float(value)/100
            opacity_label.configure(text=f'{round(float(value))}%')
            self.root.attributes('-alpha', alpha)
            if self.chart_window and self.chart_window.winfo_exists():
                self.chart_window.attributes('-alpha', alpha)
        ttk.Scale(opacity_row, from_=60, to=100, variable=opacity, command=preview_alpha).pack(side='left', fill='x', expand=True, padx=10)
        def cancel_settings():
            preview_alpha(self.cfg['opacity']*100)
            win.destroy()
        win.protocol('WM_DELETE_WINDOW', cancel_settings)
        ttk.Label(frame, text='请确保 Clash 将代理测试地址分流到代理节点。\n如需比较代理额外耗时，可把两路设为同一可直连地址。\nHTTP 错误也计响应耗时；TUN / VPN 可能接管直连。\n请选择 HTTP / Mixed 端口，暂不支持 SOCKS 端口。', foreground='#666666').grid(row=11, column=0, sticky='w', pady=12)

        def apply():
            url, proxy_url, proxy = entries['url'].get().strip(), entries['proxy_url'].get().strip(), entries['proxy'].get().strip()
            try:
                parsed = urlsplit(url)
                parsed_proxy = urlsplit(proxy_url)
                p = urlsplit(proxy)
                interval = int(entries['interval'].get())
                if parsed.scheme != 'https' or not parsed.hostname or parsed_proxy.scheme != 'https' or not parsed_proxy.hostname or p.scheme != 'http' or not p.hostname or not p.port or not 2 <= interval <= 300:
                    raise ValueError()
            except ValueError:
                messagebox.showerror('设置无效', '请填写 HTTPS 地址、http://主机:端口，以及 2–300 秒的整数。', parent=win)
                return
            direct_changed = url != self.cfg['url']
            proxy_changed = proxy_url != self.cfg['proxy_url'] or proxy != self.cfg['proxy']
            self.cfg.update(url=url, proxy_url=proxy_url, proxy=proxy, interval=interval, topmost=top.get(), opacity=opacity.get()/100)
            if direct_changed:
                self.samples['direct'] = []
            if proxy_changed:
                self.samples['proxy'] = []
            if direct_changed or proxy_changed:
                self.save_history()
            self.root.attributes('-topmost', top.get())
            if self.chart_window and self.chart_window.winfo_exists():
                self.chart_window.attributes('-topmost', top.get())
            self.save()
            # Let in-flight requests finish before restarting with the new settings.
            self.generation += 1
            self.running = False
            self.stats = {'direct': [], 'proxy': []}
            self.details = {}
            self.next_run = 0
            win.destroy()
        ttk.Button(frame, text='保存并测试', command=apply).grid(row=12, column=0, sticky='e')

    def close(self):
        self.closed = True
        self.save()
        self.save_history()
        self.diag.close()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
    ctypes.windll.kernel32.CreateEventW.restype = ctypes.c_void_p
    ctypes.windll.kernel32.SetEvent.argtypes = [ctypes.c_void_p]
    ctypes.windll.kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, 'Local\\LatencyDeskWindow')
    already_running = ctypes.windll.kernel32.GetLastError() == 183
    restore_event = ctypes.windll.kernel32.CreateEventW(None, False, False, 'Local\\LatencyDeskRestore')
    if already_running:
        ctypes.windll.kernel32.SetEvent(restore_event)
    else:
        app = App()
        app.restore_event = restore_event
        if '--charts' in __import__('sys').argv:
            app.root.after(200, app.show_charts)
        app.run()
