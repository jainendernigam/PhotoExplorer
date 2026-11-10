"""Crash-safe process pool shared by the indexing and face phases.

Jobs stream in from an iterator (so millions never sit in memory), at most
``max_inflight`` are queued, and results are handed back in batches. If a
worker dies hard (a native library crashing on a corrupt file), the pool is
rebuilt; the jobs that were in flight are retried one at a time so the one
real culprit is found and reported, and everything else still completes.
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from typing import Callable, Iterable

log = logging.getLogger(__name__)


def run_pool(
    jobs: Iterable[tuple],
    fn: Callable[[tuple], dict],
    *,
    initializer: Callable,
    initargs: tuple,
    workers: int,
    cancel: threading.Event,
    on_results: Callable[[list[dict]], None],
    on_crash: Callable[[tuple], dict],
    batch: int = 200,
) -> None:
    """Run ``fn(job)`` for every job. ``fn`` must return a dict and should
    catch its own exceptions; ``on_crash(job)`` builds the result for a job
    that crashed its worker process."""
    def pool_factory(n):
        return ProcessPoolExecutor(max_workers=n, mp_context=mp.get_context("spawn"),
                                   initializer=initializer, initargs=initargs)

    suspects = _run(iter(jobs), fn, pool_factory, workers, workers * 4, cancel, on_results, batch)
    for job in suspects:
        if cancel.is_set():
            return
        if _run(iter([job]), fn, pool_factory, 1, 1, cancel, on_results, batch):
            on_results([on_crash(job)])


def _run(jobs, fn, pool_factory, workers, max_inflight, cancel, on_results, batch) -> list[tuple]:
    suspects: list[tuple] = []
    buf: list[dict] = []
    while True:
        pool = pool_factory(workers)
        inflight: dict = {}
        broken = exhausted = False
        last_flush = time.monotonic()
        try:
            while True:
                while not exhausted and len(inflight) < max_inflight and not cancel.is_set():
                    job = next(jobs, None)
                    if job is None:
                        exhausted = True
                        break
                    inflight[pool.submit(fn, job)] = job
                if not inflight:
                    break
                done, _ = wait(list(inflight), timeout=0.5, return_when=FIRST_COMPLETED)
                for f in done:
                    job = inflight.pop(f)
                    try:
                        buf.append(f.result())
                    except BrokenProcessPool:
                        broken = True
                        suspects.append(job)
                    except Exception as e:
                        buf.append({"id": job[0], "error": f"{type(e).__name__}: {e}"[:500]})
                if broken:
                    suspects.extend(inflight.values())
                    inflight.clear()
                    break
                if len(buf) >= batch or (buf and time.monotonic() - last_flush > 1):
                    on_results(buf)
                    buf = []
                    last_flush = time.monotonic()
                if cancel.is_set():
                    break
        finally:
            pool.shutdown(wait=not broken, cancel_futures=True)
            if buf:
                on_results(buf)
                buf = []
        if not broken or cancel.is_set():
            return suspects
        log.warning("worker pool crashed; restarting (%d suspect jobs)", len(suspects))
