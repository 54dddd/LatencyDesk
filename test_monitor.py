import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock

from latency_plot import bucket_samples, paint_series
from probe_workers import ProbeWorkers


class PlotTests(unittest.TestCase):
    def test_dense_history_keeps_spikes_and_failures_with_bounded_output(self):
        points = [[i, 20, 2] for i in range(0, 7200, 2)]
        points[100][1] = 9000
        points[101][1] = None
        result = bucket_samples(points, 0, 7200, 100)
        self.assertLessEqual(len(result), 100)
        self.assertEqual(max(b['high'] or 0 for b in result), 9000)
        self.assertTrue(any(b['failed'] for b in result))
        canvas = Mock()
        paint_series(canvas, points, 0, 7200, 0, 300, 0, 100, 10000, '#68dec1')
        self.assertLess(canvas.create_line.call_count, 250)

    def test_outside_window_ignored_and_failure_breaks_curve(self):
        result = bucket_samples([[-5, 999, 5], [10, 20, 5], [15, None, 5], [20, 25, 5], [400, 999, 5]], 0, 300, 100)
        self.assertEqual(len(result), 3)
        self.assertTrue(result[1]['failed'])
        self.assertTrue(result[2]['break_before'])
        self.assertEqual(max(b['high'] or 0 for b in result), 25)

    def test_real_sampling_gap_is_shaded_and_never_connected(self):
        canvas = Mock()
        paint_series(canvas, [[10, 20, 5], [250, 22, 5]], 0, 300, 0, 300, 0, 30, 100, '#68dec1')
        canvas.create_rectangle.assert_called_once()
        self.assertEqual(canvas.create_line.call_count, 0)


class ExitTests(unittest.TestCase):
    def test_shutdown_cancels_queue_and_rejects_new_work(self):
        workers = ProbeWorkers(1)
        started, release = threading.Event(), threading.Event()
        def blocked():
            started.set()
            release.wait(2)
            return 42
        future = workers.submit(blocked)
        self.assertTrue(started.wait(1))
        queued = workers.submit(lambda: 99)
        try:
            workers.shutdown()
            self.assertTrue(queued.cancelled())
            with self.assertRaises(RuntimeError):
                workers.submit(lambda: 1)
        finally:
            release.set()
        self.assertEqual(future.result(timeout=1), 42)

    def test_close_with_blocked_network_workers_really_exits_process(self):
        script = '''
import tempfile, threading
from pathlib import Path
from unittest.mock import patch
import latency_desk as appmod
with tempfile.TemporaryDirectory() as d:
    with patch.object(appmod, 'CONFIG', Path(d)/'settings.json'), patch.object(appmod, 'HISTORY', Path(d)/'history.json'), patch.object(appmod, 'discover_proxy', return_value='http://127.0.0.1:7890'):
        a = appmod.App()
        began = [threading.Event(), threading.Event()]
        def blocked(event):
            event.set()
            threading.Event().wait(60)
        a.pool.submit(blocked, began[0])
        a.diag.pool.submit(blocked, began[1])
        assert all(event.wait(1) for event in began)
        a.close()
        a.close()
        a.tick()
        assert a.closed and a.diag.closed
        print('closed')
'''
        environment = os.environ.copy()
        result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).parent,
                                capture_output=True, text=True, timeout=5, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('closed', result.stdout)


if __name__ == '__main__':
    unittest.main()
