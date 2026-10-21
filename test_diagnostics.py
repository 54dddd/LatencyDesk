import concurrent.futures
import importlib
import io
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from diagnostics import Diagnostics, classify_event, https_probe, ping_gateway, proxy_port_probe, network_info


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.d = Diagnostics(self.temp.name)
        self.cfg = dict(gateway='192.0.2.1', proxy='http://127.0.0.1:7890', url='https://a.test/',
                        proxy_url='https://b.test/', domestic_url='https://c.test/', foreign_url='https://d.test/',
                        watch_codex=False, codex_log=str(Path(self.temp.name)/'missing.sqlite'))

    def tearDown(self):
        self.d.close()
        self.temp.cleanup()

    def test_event_classification_ignores_unrelated_messages(self):
        self.assertIsNone(classify_event('tool_runner', 'ERROR', 'connection failed in user output'))
        self.assertIsNone(classify_event('codex_core::responses_retry', 'INFO', 'error=timeout'))
        self.assertEqual(classify_event('codex_core::responses_retry', 'WARN', 'stream disconnected: os error 10054'), '连接被重置')
        self.assertEqual(classify_event('codex_core::responses_retry', 'WARN', 'stream disconnected: error decoding response body'), '响应流读取失败')
        self.assertEqual(classify_event('codex_api::endpoint::responses_websocket', 'ERROR', 'websocket error: TLS certificate'), 'TLS 连接异常')

    def test_read_only_log_poll_and_incremental_cursor(self):
        path = Path(self.temp.name)/'logs.sqlite'
        c = sqlite3.connect(path)
        c.execute('create table logs (id integer primary key, ts integer, level text, target text, feedback_log_body text)')
        c.execute('insert into logs values (1,?,?,?,?)', (int(time.time()), 'WARN', 'codex_core::responses_retry', 'stream disconnected: timeout secret=NOT_EXPORTED'))
        c.execute('insert into logs values (2,?,?,?,?)', (int(time.time()), 'ERROR', 'tool', 'unrelated'))
        c.commit()
        events, _ = self.d.poll_logs(str(path))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['label'], '请求超时')
        self.assertNotIn('secret', json.dumps(events))
        self.assertEqual(self.d.poll_logs(str(path))[0], [])
        c.execute('insert into logs values (3,?,?,?,?)', (int(time.time()), 'ERROR', 'codex_api::endpoint::responses_websocket', 'websocket error 10054'))
        c.commit()
        self.assertEqual(len(self.d.poll_logs(str(path))[0]), 1)
        self.assertEqual(c.execute('select count(*) from logs').fetchone()[0], 3)
        c.close()

    def test_missing_log_is_available_as_status(self):
        self.assertIn('未找到', self.d.poll_logs(self.cfg['codex_log'])[1])

    def test_history_expires_and_target_changes_clear_only_affected_route(self):
        now = time.time()
        self.d.record('direct', {'ms': 100, 'kind': 'ok'}, self.cfg['url'], now-7300)
        self.d.record('proxy', {'ms': 200, 'kind': 'ok'}, 'old')
        self.d.prune()
        self.assertFalse(self.d.records['direct'])
        self.d.record('direct', {'ms': 123, 'kind': 'ok'}, self.cfg['url'])
        with patch.object(self.d, 'submit'):
            self.d.tick(self.cfg)
        self.assertFalse(self.d.records['proxy'])
        self.assertEqual(self.d.records['direct'][0]['ms'], 123)
        self.d.save()
        other = Diagnostics(self.temp.name)
        self.assertEqual(other.records['direct'][0]['ms'], 123)
        other.close()

    def test_http_refusal_remains_transport_reachable(self):
        with patch('diagnostics.http.client.HTTPSConnection') as cls:
            connection = cls.return_value
            connection.getresponse.return_value.status = 403
            result = https_probe('https://target.test/', 'http://127.0.0.1:7890')
            connection.set_tunnel.assert_called_once_with('target.test', 443)
            self.assertEqual(cls.call_args.kwargs['timeout'], 15)
            connection.sock.settimeout.assert_called_once_with(8)
            self.assertEqual(result['status'], 403)
            self.assertEqual(result['kind'], 'http_error')
            self.assertIsNotNone(result['ms'])
            self.assertIn('connect_ms', result)
            connection.close.assert_called_once()

    def test_transport_failure_closes_connection(self):
        with patch('diagnostics.http.client.HTTPSConnection') as cls:
            cls.return_value.connect.side_effect = TimeoutError('test timeout')
            result = https_probe('https://target.test/')
            self.assertIsNone(result['ms'])
            self.assertEqual(result['kind'], 'transport_error')
            cls.return_value.close.assert_called_once()

    def test_tls_timeout_is_identified_without_marking_proxy_port_dead(self):
        with patch('diagnostics.http.client.HTTPSConnection') as cls:
            cls.return_value.connect.side_effect = TimeoutError('The handshake operation timed out')
            result = https_probe('https://target.test/', 'http://127.0.0.1:7890')
            self.assertEqual(result['error'], 'tls_timeout')
            self.assertIsNone(result['ms'])

    def test_header_timeout_keeps_successful_connect_timing(self):
        with patch('diagnostics.http.client.HTTPSConnection') as cls:
            cls.return_value.getresponse.side_effect = TimeoutError('read timed out')
            result = https_probe('https://target.test/')
            self.assertEqual(result['error'], 'response_timeout')
            self.assertIn('connect_ms', result)

    def test_explicit_direct_ignores_proxy_environment(self):
        with patch.dict('os.environ', {'HTTPS_PROXY': 'http://bad.test:99'}), patch('diagnostics.http.client.HTTPSConnection') as cls:
            cls.return_value.getresponse.return_value.status = 204
            https_probe('https://target.test/')
            self.assertEqual(cls.call_args.args[:2], ('target.test', 443))
            cls.return_value.set_tunnel.assert_not_called()

    def test_local_proxy_port_probe(self):
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0)); server.listen()
            result = proxy_port_probe(f'http://127.0.0.1:{server.getsockname()[1]}')
            self.assertEqual(result['kind'], 'ok')

    def test_gateway_loopback_and_missing_gateway(self):
        self.assertEqual(ping_gateway('')['kind'], 'unavailable')
        result = ping_gateway('127.0.0.1')
        if result['kind'] == 'timeout':
            self.skipTest('System ICMP loopback is not responding (also reproduced with Windows ping.exe)')
        self.assertEqual(result['kind'], 'ok')

    def test_physical_gateway_preferred_over_virtual_adapter(self):
        routes = [{'gateway':'10.0.0.1','name':'TUN','physical':False,'metric':1},
                  {'gateway':'192.168.1.1','name':'Wi-Fi','physical':True,'metric':25}]
        with patch('diagnostics.subprocess.run') as run:
            run.return_value.stdout=json.dumps(routes).encode()
            self.assertEqual(network_info()['gateway'], '192.168.1.1')

    def test_summary_requires_repeated_evidence(self):
        for _ in range(2): self.d.record('proxy_port', {'ms':None}, self.cfg['proxy'])
        self.assertIn('积累', self.d.summary())
        self.d.record('proxy_port', {'ms':None}, self.cfg['proxy'])
        self.assertIn('代理端口', self.d.summary())


class GuiTests(unittest.TestCase):
    def test_original_float_detail_tabs_axes_events_export_and_settings(self):
        import latency_desk as appmod
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'settings.json').write_text(json.dumps({'opacity': 0.88,
                'proxy_url': 'https://chatgpt.com/', 'x': 80, 'y': 100}), encoding='utf-8')
            with patch.object(appmod, 'CONFIG', Path(directory)/'settings.json'), patch.object(appmod, 'HISTORY', Path(directory)/'history.json'), \
                 patch.object(appmod, 'discover_proxy', return_value='http://127.0.0.1:7890'), patch.object(appmod.Diagnostics, 'tick'):
                a = appmod.App()
                a.next_run = dict.fromkeys(a.next_run, float('inf'))
                try:
                    a.root.update_idletasks()
                    self.assertEqual(set(a.values), {'direct','proxy'})
                    self.assertEqual(a.root.winfo_width(), appmod.px(286))
                    self.assertEqual(a.root.attributes('-alpha'), 1.0)
                    self.assertEqual(a.cfg['display_version'], 1)
                    self.assertEqual(a.cfg['proxy_url'], 'https://chatgpt.com/')
                    self.assertEqual(appmod.px((10, 20)), (appmod.px(10), appmod.px(20)))
                    for label in a.values.values():
                        self.assertGreaterEqual(label.winfo_width(), label.winfo_reqwidth())
                    # The context menu has no layout; the preceding button must fit.
                    footer = a.root.winfo_children()[-2]
                    self.assertLessEqual(footer.winfo_y()+footer.winfo_reqheight(), a.root.winfo_height())
                    now = time.time()
                    a.samples = {k:[[now-600+i*5,100+i%15,5] for i in range(121)] for k in ('direct','proxy')}
                    a.diag.mark('test event')
                    for key, _, _ in appmod.LAYERS:
                        for i in range(61): a.diag.record(key, {'ms':50+i%13,'kind':'ok'}, a.diag.signatures(a.cfg)[key], now-600+i*10)
                    a.show_charts(); a.root.update(); a.draw_charts(); a.draw_diagnostics()
                    self.assertIn('5 分钟', a.chart_heading.cget('text'))
                    a.chart_range.set('2 小时'); a.draw_charts(); a.draw_diagnostics()
                    self.assertIn('2 小时', a.chart_heading.cget('text'))
                    self.assertLess(len(a.canvases['direct'].find_all()), 200)
                    self.assertEqual(len(a.event_tree.get_children()), 1)
                    self.assertEqual(a.chart_window.winfo_width(), appmod.px(520))
                    self.assertTrue(all(c.winfo_height()>=appmod.px(125) for c in a.canvases.values()))
                    a.chart_window.destroy(); a.show_charts(); a.root.update(); a.draw_diagnostics()
                    self.assertEqual(len(a.event_tree.get_children()), 1)
                    a.diagnostic_settings(); a.root.update_idletasks(); a.diag_dialog.destroy()
                    target = Path(directory)/'report.zip'
                    with patch('tkinter.filedialog.asksaveasfilename', return_value=str(target)), patch.object(appmod.messagebox,'showinfo'):
                        a.export_diagnostics()
                    with zipfile.ZipFile(target) as z:
                        self.assertEqual(set(z.namelist()), {'samples.csv','events.csv','environment.json'})
                        self.assertNotIn('feedback_log_body', z.read('events.csv').decode('utf-8-sig'))
                    # Verify independent front requests still use their own targets.
                    calls=[]
                    def submit(fn,*args):
                        calls.append(args);f=concurrent.futures.Future();f.set_result({'ms':123,'kind':'ok','detail':'HTTP 200'});return f
                    a.refresh()
                    with patch.object(a.pool,'submit',side_effect=submit): a.tick();a.tick()
                    self.assertEqual(calls[0], (a.cfg['url'],None))
                    self.assertEqual(calls[1], (a.cfg['proxy_url'],a.cfg['proxy']))
                    self.assertIn('本次 00:00:', a.status.cget('text'))
                finally:
                    a.close()

    def test_slow_proxy_does_not_block_direct_or_apply_stale_results(self):
        import latency_desk as appmod
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(appmod, 'CONFIG', Path(directory)/'settings.json'), patch.object(appmod, 'HISTORY', Path(directory)/'history.json'), \
                 patch.object(appmod, 'discover_proxy', return_value='http://127.0.0.1:7890'), patch.object(appmod.Diagnostics, 'tick'):
                a = appmod.App()
                futures = []
                def submit(fn, *args):
                    future = concurrent.futures.Future()
                    futures.append((args, future))
                    return future
                try:
                    with patch.object(a.pool, 'submit', side_effect=submit):
                        a.tick()
                        self.assertEqual(len(futures), 2)
                        futures[0][1].set_result(dict(ms=20, detail='HTTP 200', kind='ok'))
                        a.tick()
                        a.next_run['direct'] = 0
                        a.tick()
                        self.assertEqual(len(futures), 3)
                        self.assertIn('proxy', a.inflight)
                        a.generation += 1
                        futures[1][1].set_result(dict(ms=999, detail='old target', kind='ok'))
                        a.tick()
                        self.assertFalse(a.samples['proxy'])
                        self.assertEqual(len(futures), 4)
                        a.close()
                        futures[-1][1].set_result(dict(ms=50, detail='late result', kind='ok'))
                        self.assertTrue(a.results.empty())
                finally:
                    a.close()

    def test_saved_display_preferences_survive_next_launch(self):
        import latency_desk as appmod
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)/'settings.json'
            config.write_text(json.dumps({'display_version': 1, 'opacity': 0.75,
                                          'x': 240, 'y': 160}), encoding='utf-8')
            Path(directory, 'history.json').write_text(json.dumps({'url': 'https://www.baidu.com/',
                'proxy_url': 'https://chatgpt.com/', 'proxy': 'http://127.0.0.1:7890',
                'samples': {'direct': [[time.time()-600, 42, 5]], 'proxy': []}}), encoding='utf-8')
            with patch.object(appmod, 'CONFIG', config), patch.object(appmod, 'HISTORY', Path(directory)/'history.json'), \
                 patch.object(appmod, 'discover_proxy', return_value='http://127.0.0.1:7890'):
                a = appmod.App()
                try:
                    self.assertEqual(a.root.attributes('-alpha'), 0.75)
                    self.assertEqual((a.cfg['x'], a.cfg['y']), (240, 160))
                    self.assertEqual(len(a.samples['direct']), 1)
                    a.next_run = dict.fromkeys(a.next_run, float('inf'))
                    with patch.object(a.diag, 'tick'), patch.object(appmod, 'paint_series') as paint:
                        a.tick()
                        self.assertIn('本次 00:00:', a.status.cget('text'))
                        self.assertEqual(paint.call_args_list[0].args[1], [])
                finally:
                    a.close()


if __name__ == '__main__':
    unittest.main()
