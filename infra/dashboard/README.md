# infra/dashboard

React + Vite + TS frontend that visualizes benchmark runs from the hosted
CuaWorld backend. Authenticates with the backend (JWT) and is deployable to
Vercel.

## Run

```bash
cd infra/dashboard
npm install
echo "VITE_API_BASE_URL=http://localhost:8000/api/v1" > .env.local   # or point at the hosted API
npm run dev
```

Serves on `http://localhost:5173`. Sign in with a backend user; run data is
fetched from the API.

### Offline mode (no backend, no login)

For local inspection without the hosted service — view runs straight from the
repo's `outputs/`:

```bash
VITE_DATA_SOURCE=local npm run dev   # or: just dashboard-local (from repo root)
```

Offline mode skips auth and reads `outputs/` via a dev-only Vite plugin
(`vite-plugins/runs-api.ts`). It's a development convenience and has no effect on
production builds (which are always backend-mode).

## Routes

- `/login` — sign in (username/email + password).
- `/` — list of runs (`GET /runs`).
- `/r/:runId` — rollouts in that run (`GET /rollouts?run_id=`), with task
  instructions resolved via `GET /tasks/:id`.
- `/r/:runId/t/:rolloutId` — trajectory player: scrub screenshots, see action +
  model thinking per frame.

## Data source

In the default (backend) mode, all data comes from the backend at
`VITE_API_BASE_URL` (defaults to `http://localhost:8000/api/v1` when unset).
`src/lib/api.ts` dispatches between the backend and the offline source and maps
both onto the UI types.

- **Auth** — `POST /auth/login` → access + refresh tokens in `localStorage`;
  `src/lib/api.ts` injects the bearer token and transparently refreshes on 401.
- **Trajectory artifacts** — `GET /rollouts/:id/artifacts` returns short-lived
  signed S3 URLs for `trajectory.jsonl` and each screenshot; the player fetches
  those directly. The S3 bucket must allow cross-origin `GET` from the dashboard
  origin (see the backend's `scripts/s3-artifacts-cors.json`).

## Deploy (Vercel)

- Set the project **Root Directory** to `infra/dashboard`.
- Framework preset **Vite**; build `npm run build`; output `dist`.
- Set `VITE_API_BASE_URL` to the hosted API (Production + Preview).
- `vercel.json` rewrites all routes to `index.html` for client-side routing.
- Ensure the backend `CORS_ORIGINS` (and the S3 bucket CORS) include the
  deployed origin.

## Stack

Vite + React 19 + TypeScript + React Router 7. No CSS framework — plain CSS
variables in `src/index.css`.
