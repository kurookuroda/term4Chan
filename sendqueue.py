"""Webhook送信をバックグラウンドで処理するキュー。

送信を担当するスレッドは1つだけで、ジョブは追加した順に1本ずつ処理する。
(並行に走らせると、同じWebhookへの送信間隔を守れなくなるため)
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import webhook

ACTIVE_STATES = ('queued', 'running')


@dataclass
class Job:
    id: int
    board: str
    thread_id: str
    start: int            # 送る範囲(1始まり、両端を含む)
    end: int
    posts: list
    urls: list
    include_images: bool
    include_url: bool
    interval: float
    state: str = 'queued'  # queued / running / done / failed / stopped / error
    processed: int = 0
    error: str = ''
    failures: dict = field(default_factory=dict)
    cancel: threading.Event = field(default_factory=threading.Event)

    @property
    def total(self) -> int:
        return len(self.posts)

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def failed_posts(self) -> int:
        """1つ以上のWebhookへの送信に失敗した投稿の数。"""
        return len({n for nums in self.failures.values() for n in nums})


def summary(job: Job) -> str:
    """完了通知用の1行。"""
    where = f'/{job.board}/ {job.thread_id}'
    if job.state == 'done':
        return f'[Job {job.id} done] {where}: {job.total} posts sent'
    if job.state == 'stopped':
        return f'[Job {job.id} stopped] {where}: {job.processed}/{job.total} processed'
    if job.state == 'error':
        return f'[Job {job.id} error] {where}: {job.error}'
    return (f'[Job {job.id} finished with errors] {where}: {job.failed_posts} of '
            f'{job.total} posts could not be sent to some webhook(s) (see: jobs)')


class SendQueue:

    def __init__(self, notify: Callable[[str], None]) -> None:
        self._notify = notify
        self._queue: queue.Queue = queue.Queue()
        self._jobs: list[Job] = []
        self._lock = threading.Lock()
        self._last_sent_at: float | None = None  # 直前のジョブが終わった時刻
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    # ---- 操作

    def submit(self, board: str, thread_id, start: int, end: int, posts: list,
               urls: list, include_images: bool, include_url: bool,
               interval: float) -> tuple[Job, int]:
        """ジョブを追加する。戻り値は (ジョブ, 追加時点で先に並んでいるジョブ数)。"""
        with self._lock:
            ahead = sum(1 for j in self._jobs if j.active)
            job = Job(id=len(self._jobs) + 1, board=board, thread_id=str(thread_id),
                      start=start, end=end, posts=list(posts), urls=list(urls),
                      include_images=include_images, include_url=include_url,
                      interval=interval)
            self._jobs.append(job)
        self._queue.put(job)
        return job, ahead

    @property
    def jobs(self) -> list[Job]:
        with self._lock:
            return list(self._jobs)

    def active_jobs(self) -> list[Job]:
        return [j for j in self.jobs if j.active]

    def get(self, job_id: int) -> Job | None:
        for job in self.jobs:
            if job.id == job_id:
                return job
        return None

    def stop(self, job_id: int) -> bool:
        """ジョブを中止する。送信中なら、いまの1件が終わったところで止まる。"""
        job = self.get(job_id)
        if job is None or not job.active:
            return False
        job.cancel.set()
        if job.state == 'queued':
            job.state = 'stopped'  # まだ始まっていないので、すぐ中止扱いにする
        return True

    def cancel_all(self) -> None:
        for job in self.active_jobs():
            self.stop(job.id)

    # ---- 送信スレッド

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job.cancel.is_set():
                job.state = 'stopped'
                continue

            job.state = 'running'

            # ジョブをまたいでも、前の送信から interval 秒は空ける
            if self._last_sent_at is not None:
                wait = job.interval - (time.monotonic() - self._last_sent_at)
                if wait > 0:
                    job.cancel.wait(wait)  # 中止されたらすぐ起きる

            try:
                def on_progress(i: int, count: int, post: dict) -> None:
                    job.processed = i

                webhook.broadcast_posts(
                    job.urls, job.board, job.posts, job.include_images,
                    job.interval, on_progress,
                    thread_id=job.thread_id if job.include_url else None,
                    cancel=job.cancel, failures=job.failures)
            except Exception as ex:
                job.error = f'{type(ex).__name__}: {ex}'
                job.state = 'error'
            else:
                if job.cancel.is_set() and job.processed < job.total:
                    job.state = 'stopped'
                elif job.processed < job.total or job.failed_posts:
                    job.state = 'failed'
                else:
                    job.state = 'done'

            self._last_sent_at = time.monotonic()

            try:
                self._notify(summary(job))
            except Exception:
                pass
