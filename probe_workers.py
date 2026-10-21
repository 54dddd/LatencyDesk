"""Small daemon worker pool for read-only probes; a stuck resolver must not keep the UI alive."""
from concurrent.futures import Future
import queue
import threading


class ProbeWorkers:
    def __init__(self, max_workers):
        self.jobs = queue.Queue()
        self.closed = False
        self.lock = threading.Lock()
        self.threads = [threading.Thread(target=self.work, name=f'probe-{i}', daemon=True)
                        for i in range(max_workers)]
        for thread in self.threads:
            thread.start()

    def work(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            future, fn, args = job
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(fn(*args))
                except BaseException as error:
                    future.set_exception(error)

    def submit(self, fn, *args):
        with self.lock:
            if self.closed:
                raise RuntimeError('Probe workers are closed')
            future = Future()
            self.jobs.put((future, fn, args))
            return future

    def shutdown(self, wait=False, cancel_futures=True):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if cancel_futures:
                while True:
                    try:
                        self.jobs.get_nowait()[0].cancel()
                    except queue.Empty:
                        break
            for _ in self.threads:
                self.jobs.put(None)
        if wait:
            for thread in self.threads:
                thread.join()
