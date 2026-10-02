# Private deployment

MiroFish runs as a single-owner workspace. The built frontend and API share one origin, and the backend requires an access key or an authenticated browser session. The default listeners are available only from your own computer.

## Configure once

Use Node.js 22.12 or newer, Python 3.11, and uv for a local installation. Docker builds its own Node and Python environments.

```sh
npm run setup:config
```

Complete the provider and Zep configuration in the root `.env` file. Keep that file private and out of version control. `MIROFISH_ACCESS_KEY` is your workspace sign-in key; use a unique random value of at least 32 characters. Provider API keys belong only in server configuration.

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

The image builds the frontend with Node 22 and installs the frozen Python lockfile without development dependencies. Runtime uses Python 3.11 and one Waitress process under UID/GID `10001:10001`. Application code is read-only to this user; uploads and logs are writable. The Compose service drops Linux capabilities and prevents gaining new privileges.

Named volumes `mirofish_uploads` and `mirofish_logs` preserve uploads, simulations, reports, and logs across container replacement. Docker initializes new volumes with the image's directory ownership. Back up these volumes and `.env` before changing versions. `docker compose down` preserves volumes; adding `--volumes` deletes them.

Older Compose versions of this project bind-mounted `./backend/uploads`. Those files remain on the host and are not automatically imported into the named volume. Before upgrading an existing deployment, stop simulation work, back up that directory, and copy its contents into the new uploads volume with ownership `10001:10001`. Do not delete the old directory until the restored projects have been verified. If you choose bind mounts instead, create their directories first and make them writable by UID/GID `10001:10001`.

## Process and network limits

Use one backend application process and one replica. Simulation runners, task status, and parts of the report workflow keep state in process memory. Multiple WSGI workers or replicas can disagree about running jobs. Waitress request threads share that state; increasing `WAITRESS_THREADS` does not add worker processes. Restarting can interrupt active work, so wait for simulations and reports to finish before upgrading.

For remote access, place an HTTPS reverse proxy in front of the loopback listener, set `MIROFISH_COOKIE_SECURE=true`, and add the browser-facing origin to `MIROFISH_ALLOWED_ORIGINS` if the proxy changes the origin seen by the backend. Keep the access key private. The application is a single-owner workspace, with no separate user accounts or per-project permissions.
