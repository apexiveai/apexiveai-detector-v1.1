# Apexive AI Trademark Conflict Detector

Trademark mark records extracted from PDFs, Excel workbooks, and standalone raster logo files are normalized into a common asset record and compared by the same visual/text matching pipeline.

## Supported input and evidence

- PDF ↔ PDF, XLSX/XLSM ↔ XLSX/XLSM, PDF ↔ Excel, and supported logo image ↔ any supported source.
- PDF extraction targets the `(540)` mark field. Pages without a text layer use Tesseract OCR to locate that field and capture a page-bounded evidence crop.
- Excel extraction checks all worksheets for image anchors and text in a `Mark` or equivalent mark/logo/device column.
- Standalone logo inputs: PNG, JPEG, BMP, WEBP, TIFF, and GIF (first frame).
- Reports retain page, sheet, row, column, and an A1-style `source_reference` where the source provides that coordinate. Each source's extracted-mark inventory is available independently of the match list.
- Application-number equality is metadata only. Exact and similarity scores are technical evidence, not a legal determination; human review is required.

## Local development

1. Install the backend dependencies from `backend\requirements.txt` into `backend\venv`.
2. Install Tesseract OCR and put `tesseract.exe` on `PATH` if scanned PDF OCR is needed.
3. Start the API from `backend`:

   ```powershell
   .\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8020
   ```

4. In a second terminal, start the frontend from `frontend`:

   ```powershell
   npm ci
   npm run dev
   ```

Vite proxies `/api` and `/health` to the local API. In development, API auth is optional. Production requires a verified bearer JWT. If the backend is not listening on `127.0.0.1:8020`, copy `frontend\.env.example` to `frontend\.env` and set `VITE_API_PROXY_TARGET` to the backend origin before starting Vite.

## API and job operations

- `POST /api/analyze`: multipart `file_a`/`file_b` or legacy `source_a`/`source_b`; returns `202` and a `job_id`.
- `GET /api/jobs/{id}`: persisted status, progress, and result summary.
- `GET /api/jobs/{id}/results` and `/assets/{A|B}`: paginated, searchable evidence/results.
- `DELETE /api/jobs/{id}`: request cooperative cancellation.
- `POST /api/jobs/{id}/retry`: manually retry a failed job, up to `MAX_JOB_ATTEMPTS`.
- `GET /api/jobs/{id}/files/{filename}`: tenant-authorized report download.
- `GET /api/audit`: recent audit events for the authenticated tenant.
- `GET /health`: checks the API and persistent SQLite storage.

PDF extraction and matching report progress in bounded page/asset loops; uploads have per-file, aggregate, archive-expansion, image-pixel, PDF-page, worksheet-size, and per-source-record limits. Worker concurrency and queue capacity are bounded. The queue runs in the API process; deploy one API process per instance. SQLite and local file storage support a persistent single-node deployment, but do not provide cross-region or horizontally shared queue/storage semantics.

## Production deployment

### Railway API + Vercel frontend

Deploy the API and frontend as separate services. Deploy the backend first so its public URL is available for the Vercel build.

1. In Railway, create a service from this repository and leave **Root Directory** at the repository root. Railway will build the root `Dockerfile`; leave the Railway Start Command blank to use its `PORT`-aware container command, and set the health check path to `/health`.
2. Add a Railway volume mounted at `/app/data`. Keep this service to **one replica**: the job queue is process-local and SQLite/local files are not shared safely across replicas.
3. Set the Railway variables:
   - `APP_ENV=production`
   - `APEXIVE_API_URL=https://<your-central-apexive-api-origin>` (origin only; no `/api` suffix)
   - `CORS_ORIGINS=https://<your-vercel-domain>` (exact browser origin, with no trailing slash)
   Configure any required JWT issuer/audience values for the central authorization setup. The API must be reachable from Railway over HTTPS.
4. Deploy the Railway service, then open `https://<your-railway-domain>/health` and confirm it returns a healthy response. Keep the generated Railway domain; it is the backend API origin.
5. Import the same repository into Vercel and set **Root Directory** to `frontend`. Add the build environment variable `VITE_API_URL=https://<your-railway-domain>` (origin only, no trailing slash), then deploy. If unset, production builds use the configured Railway service domain; local Vite development continues to use its `/api` proxy. The checked-in `frontend/vercel.json` enables SPA route fallback for direct links and refreshes.
6. If you use a custom Vercel domain, update Railway's `CORS_ORIGINS` to that exact origin and redeploy the backend. Add each allowed Vercel preview origin explicitly if previews need API access; do not use `*`.

`VITE_API_URL` is embedded in the frontend at build time, so redeploy Vercel after changing it. Railway's `PORT` is supplied by the platform; the container binds to it and its Docker health check follows it. Keep the Railway volume attached when redeploying so SQLite records and uploaded reports persist.

The Docker Compose setup builds a non-root FastAPI backend and static Nginx frontend, serves the API through the same origin, persists job data in a named volume, and health-checks both services.

1. Copy `.env.example` to `.env` and configure either a unique high-entropy `JWT_SECRET` (at least 32 random bytes) or a trusted `JWT_PUBLIC_KEY` and matching `JWT_ALGORITHMS`. Never commit `.env`.
2. Configure `JWT_ISSUER`/`JWT_AUDIENCE` when applicable. In the integrated deployment, the bearer token is validated against the central Apexive API; set `APEXIVE_API_URL` to its HTTPS origin (without an `/api` suffix). The API must be network reachable from the Detector. Requests fail closed if the central authorization check fails.
3. Set `APEXIVE_AUTH_TIMEOUT` and `APEXIVE_AUTH_RECHECK_SECONDS` to bound authorization calls and recheck long-running queued work (defaults: 5 seconds and 30 seconds; rechecks are clamped to at least 5 seconds). A requester's bearer token is held only in worker memory for deferred authorization checks; it is not persisted in job data. Work must stop if authorization can no longer be verified.
4. Configure per-file and aggregate upload limits, including `MAX_FILE_BYTES` and `MAX_UPLOAD_BYTES`. Set a matching maximum request-body size at the public ingress/reverse proxy: application checks reject declared oversized bodies and each parsed file is bounded, but a missing `Content-Length` alone is not a reliable ingress resource limit.
5. Set `CORS_ORIGINS` to the exact trusted browser origins if the UI/API are not same-origin. For a separately hosted frontend and API, also set `VITE_API_URL` to the API origin when building the frontend; the API must allow the frontend's exact origin in `CORS_ORIGINS`.
6. On a Docker host with the Compose plugin, run `docker compose up --build -d`; verify `http://localhost:${WEB_PORT:-8080}/healthz`, `/health`, and `docker compose ps`.
7. Terminate public TLS at a trusted reverse proxy/load balancer. Keep the backend port private; apply network policy and secret management at the hosting platform.

The SQLite database and uploads/reports live together under `/app/data` in the `trademark-data` volume. Back up the complete volume using a consistent volume snapshot or a stopped-service backup, and verify restores periodically; the application does not configure an external backup destination. Container logs go to stdout/stderr for the host's log collector. Health endpoints are included; external alerting/metrics and platform-specific deployment credentials remain infrastructure configuration.

`MAX_FILE_BYTES`, `MAX_UPLOAD_BYTES`, `MAX_PDF_PAGES`, `MAX_PDF_PAGE_PIXELS`, `MAX_IMAGE_PIXELS`, `MAX_ASSETS_PER_SOURCE`, `MAX_WORKSHEET_ROWS`, `MAX_WORKSHEET_COLUMNS`, `MAX_WORKERS`, `MAX_QUEUED_JOBS`, `MAX_JOB_ATTEMPTS`, `JOB_RETENTION_DAYS`, and `AUDIT_RETENTION_DAYS` control resource usage and retention. Completed/error/cancelled job files are removed after the retention interval; audit events are retained for their configured period in SQLite. Restart-interrupted jobs are marked failed and can be retried.

## Verification

From `backend`, run `.\venv\Scripts\python.exe -m unittest discover -s tests -v`. From `frontend`, run `npm run build`.
