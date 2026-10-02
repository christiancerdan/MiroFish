#!/usr/bin/env python3
"""Install locked API and simulation interpreters without changing credentials."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--simulation-only', action='store_true')
    args = parser.parse_args()
    uv = shutil.which('uv')
    if not uv:
        parser.error('uv is required; install it before running setup:backend')
    backend = Path(__file__).resolve().parents[1] / 'backend'
    if not args.simulation_only:
        env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(backend / '.venv'))
        subprocess.run([uv, 'sync', '--locked', '--project', str(backend)], env=env, check=True)
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(backend / '.venv-simulation'))
    subprocess.run([
        uv, 'sync', '--locked', '--project', str(backend), '--extra', 'simulation', '--no-dev',
    ], env=env, check=True)
    print('Installed locked API and simulation runtimes.' if not args.simulation_only else 'Installed locked simulation runtime.')


if __name__ == '__main__':
    main()
