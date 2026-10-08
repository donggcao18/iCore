"""Report slow requests and enforce elapsed deadlines on Unix main threads."""

from contextlib import contextmanager
import signal
import threading
import time


class RequestDeadlineExceeded(TimeoutError):
    pass


@contextmanager
def request_watchdog(model, seconds, interval=30):
    started = time.monotonic()
    stopped = threading.Event()
    expired = False
    alarm = (hasattr(signal, 'setitimer')
             and threading.current_thread() is threading.main_thread()
             and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0))
    previous_handler = None

    def deadline(signum, frame):
        nonlocal expired
        expired = True
        raise RequestDeadlineExceeded(f'LLM request exceeded {seconds:g}s for {model}.')

    def progress():
        while not stopped.wait(interval):
            elapsed = time.monotonic() - started
            print(f'LLM still waiting: model={model}; elapsed={elapsed:.0f}s', flush=True)

    print(f'LLM request started: model={model}; '
          f'{"elapsed deadline" if alarm else "I/O timeout"}={seconds:g}s', flush=True)
    if alarm:
        previous_handler = signal.signal(signal.SIGALRM, deadline)
        signal.setitimer(signal.ITIMER_REAL, seconds)
    reporter = threading.Thread(target=progress, daemon=True)
    reporter.start()
    try:
        yield
    finally:
        if alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
        stopped.set()
        reporter.join(timeout=1)
        print(f'LLM request ended: model={model}; elapsed={time.monotonic() - started:.1f}s', flush=True)
        # SDKs may wrap an exception raised during a blocking network read.
        # Keep deadline failures distinct so they are not automatically retried.
        if expired:
            raise RequestDeadlineExceeded(f'LLM request exceeded {seconds:g}s for {model}.')
