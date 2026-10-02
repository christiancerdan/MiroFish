#!/usr/bin/env python3
"""Apply bounds before importing OASIS; this is not filesystem isolation.

The parent starts a fresh process group. The independent watchdog also stops a
worker after its API parent disappears, including abrupt API termination.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import runpy
import signal
import sys
import threading
import time


def write_record(directory: Path, name: str, data: dict) -> None:
    target = directory / name
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(data), encoding='utf-8')
    temporary.replace(target)


def apply_limits(cpu_seconds: int, memory_mb: int) -> dict:
    limits = {'cpu_limit_enforced': False, 'memory_limit_enforced': False}
    if os.name == 'posix':
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        current = resource.getrlimit(resource.RLIMIT_CPU)[1]
        cpu = cpu_seconds if current == resource.RLIM_INFINITY else min(cpu_seconds, current)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        limits['cpu_limit_enforced'] = True
        # Darwin's address-space limit is unsupported/unreliable. Do not claim
        # it as enforced; Linux containers additionally apply their cgroup limit.
        if sys.platform.startswith('linux'):
            current = resource.getrlimit(resource.RLIMIT_AS)[1]
            memory = memory_mb * 1024 * 1024
            if current != resource.RLIM_INFINITY:
                memory = min(memory, current)
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
            limits['memory_limit_enforced'] = True
    return limits


def stop_worker(directory: Path, code: str) -> None:
    write_record(directory, 'failure.json', {'code': code})
    if os.name == 'posix' and os.getpgrp() == os.getpid():
        os.killpg(os.getpgrp(), signal.SIGKILL)
    os._exit(124)


def watch_parent_and_deadline(parent_pid: int, deadline: float, directory: Path) -> None:
    while True:
        if os.getppid() != parent_pid:
            stop_worker(directory, 'interrupted')
        if time.monotonic() >= deadline:
            stop_worker(directory, 'simulation_timeout')
        time.sleep(min(0.2, max(0.01, deadline - time.monotonic())))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script', required=True)
    parser.add_argument('--runtime-dir', type=Path, required=True)
    parser.add_argument('--parent-pid', type=int, required=True)
    parser.add_argument('--wall-seconds', type=float, required=True)
    parser.add_argument('--cpu-seconds', type=int, required=True)
    parser.add_argument('--memory-mb', type=int, required=True)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if min(args.wall_seconds, args.cpu_seconds, args.memory_mb) <= 0:
        parser.error('Worker resource limits must be positive')
    os.environ['MIROFISH_SIMULATION_WORKER'] = '1'
    os.umask(0o077)
    limits = apply_limits(args.cpu_seconds, args.memory_mb)
    write_record(args.runtime_dir, 'isolation.json', {
        **limits, 'wall_seconds': args.wall_seconds, 'cpu_seconds': args.cpu_seconds,
        'memory_mb': args.memory_mb, 'filesystem_sandbox': False,
    })
    watchdog = threading.Thread(target=watch_parent_and_deadline, args=(
        args.parent_pid, time.monotonic() + args.wall_seconds, args.runtime_dir,
    ), daemon=True)
    watchdog.start()
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    sys.argv = [args.script, *arguments]
    runpy.run_path(args.script, run_name='__main__')


if __name__ == '__main__':
    main()
