# evalkit UI

The evalkit web frontend: datasets, experiments, scorecards, the ATIF
trajectory viewer, a runs (sessions) browser, harness checks, and user admin.

## Development

```bash
npm install
npm run dev        # http://127.0.0.1:5180, proxies /api → http://localhost:8020
```

Run the server alongside it (`evalkit serve --config config.yaml` defaults to
port 8020). Sign in with an evalkit user (see `evalkit users --help`).

## Build & test

```bash
npm run build      # tsc -b && vite build → dist/ (served by the evalkit server)
npm test           # vitest
```
