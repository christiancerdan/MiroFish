# Private deployment

MiroFish runs as a single-owner workspace. The built frontend and API share one origin, and the backend requires an access key or an authenticated browser session. The default listeners are available only from your own computer.

## Configure once

Use Node.js 22.12 or newer, Python 3.11 or 3.12, and uv for a local installation. Docker builds its own Node and Python environments.

```sh
npm run setup:config
```

Complete the provider configuration and select the local memory backend (or configure Zep Cloud) in the root `.env` file. Keep that file private and out of version control. `MIROFISH_ACCESS_KEY` is your workspace sign-in key; use a unique random value of at least 32 characters. Provider API keys belong only in server configuration.

| Setting | Purpose |
| --- | --- |
| `MIROFISH_ACCESS_KEY` | Required private workspace key. Enter it on the sign-in screen. |
| `MIROFISH_COOKIE_SECURE` | Set `false` for local HTTP. Set `true` when accessed through HTTPS. |
| `MIROFISH_ALLOWED_ORIGINS` | Optional comma-separated trusted browser origins, including scheme and port. Same-origin requests work without an additional entry. |
| `FLASK_HOST` / `FLASK_PORT` | Local production listener defaults to `127.0.0.1:5001`. |
| `WAITRESS_THREADS` | Request threads in the one backend process; defaults to `8`. |

Browser login uses an HttpOnly, SameSite=Strict session cookie. Writes also require a CSRF token, which the frontend holds in memory. Reloading the page verifies the cookie with the server. Programmatic clients can send `Authorization: Bearer <MIROFISH_ACCESS_KEY>` instead of using the browser session.

## Run locally

```sh
npm run setup:all
npm run build
npm start
```

Open `http://127.0.0.1:5001` and sign in with your access key. `npm start` launches Waitress and serves `frontend/dist` through Flask; rebuild after changing frontend source. Keep `FLASK_DEBUG=false`.

`npm run dev` remains available for development. Vite listens on `127.0.0.1:3000` and proxies `/api` to `127.0.0.1:5001`. Browser API requests use the current origin by default, so no frontend API URL is needed. The production launch does not run Vite.

## Run with Docker Compose

After configuring `.env`:

```sh
docker compose up --build -d
docker compose ps
```

Open `http://127.0.0.1:3000`. Compose publishes only `127.0.0.1:3000:5001`. The container listens on all interfaces internally so Docker can forward requests; the host port remains bound to loopback. The health check calls `/health` without requiring a login.

The image builds the frontend with Node 22 and installs the frozen Python lockfile without development dependencies into two interpreters: `.venv` for the API and `.venv-simulation` with the `simulation` extra for workers. Runtime uses Python 3.11 and one Waitress process under UID/GID `10001:10001`. Application code is read-only to this user; uploads and logs are writable. The Compose service drops Linux capabilities, prevents gaining new privileges, mounts the image read-only, and sets a 12 GiB memory limit, two CPUs, and a 256-process limit for the complete container.

Named volumes `mirofish_uploads` and `mirofish_logs` preserve uploads, simulations, reports, and logs across container replacement. Docker initializes new volumes with the image's directory ownership. Back up these volumes and `.env` before changing versions. `docker compose down` preserves volumes; adding `--volumes` deletes them.

Older Compose versions of this project bind-mounted `./backend/uploads`. Those files remain on the host and are not automatically imported into the named volume. Before upgrading an existing deployment, stop simulation work, back up that directory, and copy its contents into the new uploads volume with ownership `10001:10001`. Do not delete the old directory until the restored projects have been verified. If you choose bind mounts instead, create their directories first and make them writable by UID/GID `10001:10001`.

## Process and network limits

Use one backend application process and one replica. Simulation process handles remain owned by one application process. Durable jobs and budgets use SQLite, but multiple WSGI workers or replicas can still disagree about live simulation handles. Waitress request threads share that state; increasing `WAITRESS_THREADS` does not add worker processes. Interrupted simulations are marked failed with `error_code=interrupted` on the next status read and require an explicit retry; saved PIDs are never used to signal an unrelated process.

For remote access, place an HTTPS reverse proxy in front of the loopback listener, set `MIROFISH_COOKIE_SECURE=true`, and add the browser-facing origin to `MIROFISH_ALLOWED_ORIGINS` if the proxy changes the origin seen by the backend. Keep the access key private. The application is a single-owner workspace, with no separate user accounts or per-project permissions.


## Simulation worker runtime

`npm run setup:all` installs two locked Python environments. The API uses
`backend/.venv`, while simulations use `backend/.venv-simulation`. The latter
contains the reviewed OASIS package and CAMEL/ML dependencies. Configuration
automatically selects that interpreter when present. `SIMULATION_PYTHON` can
point to another prepared Python interpreter; the API default remains its own
interpreter when the separate environment is absent.

To install only the simulation environment, run
`python3 scripts/setup_backend.py --simulation-only`. Both installations use the
same lock and neither operation modifies `.env`. The API-only installation is
`cd backend && uv sync --locked`; simulation start requires the simulation extra
in the selected interpreter. See [dependency review](dependency-security.md) for
the OASIS packaging patch and upstream provenance.

| Worker setting | Default | Enforcement |
| --- | --- | --- |
| `SIMULATION_MAX_WALL_SECONDS` | 3600 | Independent watchdog terminates the process group on POSIX; bounds the complete run including interview wait time. |
| `SIMULATION_MAX_CPU_SECONDS` | 1800 | POSIX CPU-time limit, applied before importing simulation libraries. |
| `SIMULATION_MAX_MEMORY_MB` | 8192 | Linux address-space limit; not claimed as enforced on macOS or Windows. Compose also bounds whole-container physical memory. |

Each launch receives only explicit LLM configuration and the shared run-budget
identifier/database path. Workspace access keys, Flask secrets, Zep keys, cloud
storage credentials, inherited Python paths, and model-hub tokens are excluded.
Simulation scripts and application configuration skip dotenv loading inside the
worker. Graph-memory ingestion stays in the API process. Worker HOME, temporary
files, and model caches live in a private `.worker-*` directory within that
simulation's data directory. These directories persist with run evidence and
can be deleted when that simulation is no longer needed.

The bootstrap records enforced limits in `.worker-*/isolation.json` and a safe
failure code when its deadline or API-parent watchdog terminates the worker.
The runner also checks the shared budget during execution and after exit, so
CAMEL swallowing a provider exception cannot turn an exhausted budget into a
successful run. The watchdog terminates orphaned workers after an abrupt API
exit. On Windows, CPU/address-space limits are unavailable and watchdog exit
bounds only the worker itself; normal API stop uses `taskkill /T` for its tree.

This is dependency, environment, and resource isolation. A local worker runs
under the same OS user and can read files that user can read. In Compose both
interpreters remain in one container; there is no separate worker container or
filesystem/network security boundary between the API and simulation code.
Separate users/containers and scoped mounts would be required to execute
untrusted extensions. Do not treat this setup as such a sandbox.

Validation of the reviewed runtime included a Linux ARM64 image build and
non-root, read-only container startup, with a successful WSGI health check.
The API interpreter had no CAMEL/OASIS installation; the separate interpreter
imported OASIS, CAMEL, and CPU-only Torch under enforced CPU and 8 GiB Linux
address-space limits. On macOS, a real synthetic OASIS post passed through the
filtered worker environment to the configured local Ollama service and matched
its persisted SQLite action trace. That check does not establish GPU support or
large-model memory requirements.
