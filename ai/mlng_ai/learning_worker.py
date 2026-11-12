"""Bounded subprocess learning. Inference never waits for model fitting."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


class Supervisor:
    def __init__(self, state, interval=60, timeout=1800):
        self.state=Path(state).resolve()
        self.interval=interval
        self.timeout=timeout
        self.stop=threading.Event()
        self.thread=threading.Thread(target=self.run,name='milg-learning-supervisor',daemon=True)
        self.child=None

    def start(self): self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=10)

    def run(self):
        from .pilot import write_json, now
        log=self.state/'learning-worker.log'
        while not self.stop.is_set():
            try:
                if log.exists() and log.stat().st_size>5_000_000:
                    log.replace(self.state/'learning-worker.previous.log')
                env=dict(os.environ,OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',MKL_NUM_THREADS='2')
                with log.open('ab') as output:
                    self.child=subprocess.Popen([sys.executable,'-X','utf8','-u','-m','mlng_ai.learning','tick',
                        '--state',str(self.state)],stdout=output,stderr=output,env=env,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                    started=time.monotonic()
                    heartbeat=0
                    while self.child.poll() is None:
                        if time.monotonic()-heartbeat>30:
                            write_json(self.state/'learning-supervisor.json',dict(updated=now(),status='checking_or_training',
                                pid=self.child.pid,elapsed_seconds=round(time.monotonic()-started,2),stopping=False))
                            heartbeat=time.monotonic()
                        if self.stop.wait(.25) or time.monotonic()-started>self.timeout:
                            self.child.terminate()
                            try:self.child.wait(timeout=5)
                            except subprocess.TimeoutExpired:self.child.kill();self.child.wait()
                            break
                    write_json(self.state/'learning-supervisor.json',dict(updated=now(),returncode=self.child.returncode,
                               elapsed_seconds=round(time.monotonic()-started,2),stopping=self.stop.is_set()))
            except Exception as exc:
                write_json(self.state/'learning-supervisor.json',dict(updated=now(),error=str(exc)))
            finally:self.child=None
            self.stop.wait(self.interval)
