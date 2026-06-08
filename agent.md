# Personal Cloud Storage Agent Guide

## What This Project Is
This repo contains:
- a FastAPI backend that lists, uploads, and deletes files in one S3 bucket
- a React frontend that calls that backend directly from the browser

The frontend is run locally with Node/Vite. The backend is run with Docker.

## Current Structure
- Backend app: `backend/app/storage_backend.py`
- Backend config module: `backend/app/config.py`
- Backend S3 service: `backend/app/services/s3_storage_service.py`
- Backend production image: `backend/dockerfile`
- Backend dev image: `backend/Dockerfile.dev`
- Backend dependencies: `backend/requirements.txt`
- Frontend app: `frontend/src/App.jsx`
- Frontend entry page: `frontend/index.html`
- Frontend package config: `frontend/package.json`
- Backend prod compose: `docker-compose.yml`
- Backend dev compose: `docker-compose.dev.yml`

## Backend Summary
- Framework: FastAPI
- AWS SDK: `boto3`
- Server: `uvicorn`
- Port: `8000`
- Required environment variable: `S3_BUCKET`
- Optional environment variable: `CORS_ORIGINS`
- Optional environment variable: `BACKEND_DOMAIN`

The backend creates an S3 client with the normal AWS credential chain. In this repo, Docker mounts the local AWS credentials directory into the container read-only.
The route file should stay focused on request models and API handlers, while shared S3 behavior lives in the service layer.
The config module should load settings from the repo root `.env` file and expose them as Python settings values instead of direct `os.environ[...]` reads in route code.

## Frontend Summary
- Stack: React with Vite
- Local dev port: `3000`
- Default backend base URL: `http://localhost:8000/api`

The frontend is not part of Docker. It talks to the backend directly over HTTP.

## API Surface
- `GET /api/` returns `{"status":"ok"}`
- `POST /api/auth/login` returns a bearer token plus the temporary user profile
- `GET /api/auth/me` returns the current authenticated user from the bearer token
- `GET /api/whoami` returns the current AWS caller identity
- `GET /api/check_bucket` returns the current authenticated user's bucket info
- `GET /api/files?prefix=...` returns the current folder prefix plus immediate child folders and files
- `POST /api/folders` creates a folder inside the provided prefix
- `POST /api/upload` uploads one multipart file, optionally into the provided folder prefix
- `GET /api/files/{key:path}/download` returns a short-lived presigned download URL
- `POST /api/files/{key:path}/rename` renames a file by copying to the corrected final name and deleting the original
- `DELETE /api/files/{key}` deletes a file, or deletes a folder only when it is empty
- `DELETE /api/delete?key=...` is an alias for delete by query string

## How To Run
Frontend:
1. Open `frontend/`
2. Run `npm install`
3. Run `npm run dev`
4. Open `http://localhost:3000`

Backend production-like Docker run:
1. From the repo root, run `docker compose up --build`
2. Open `http://localhost:8000/docs` for Swagger

Backend Docker dev run:
1. From the repo root, run `docker compose -f docker-compose.dev.yml up --build`
2. This runs `uvicorn` with `--reload`
3. Open `http://localhost:8000/docs`

## Git workflow & permission
- Git CLI commands that change repository state require user permission.
- Read-only Git CLI commands that do not change code or repository state, such as `git status`, `git log`, and `git diff`, can be run without asking first.
- Before running `git add` and `git commit`, list the exact CLI commands and the commit message, and make sure the message matches the actual work done.
- Use `git commit -m "Title" -m "Detailed description"` so commits include both a title and a description.
- Before running `git push`, ask the user and list the exact CLI command.
- Every time a branch is switched or created, state the current branch afterward.
- Dev containers should rely on the bind mount from `docker-compose.dev.yml`.
- Keep generated cache and log files out of git.

## S3 and API Permission
- Ask for user permission before any S3 read, S3 write, or API call that reads from or writes to S3
- Do not use the app API or direct AWS calls for live verification without user approval
- Treat upload, delete, rename, download-link generation, and list checks against the real bucket as permission-gated actions

## Docker workflow & permission
- Dev start: `docker compose -f docker-compose.dev.yml up`
- Dev rebuild image: `docker compose -f docker-compose.dev.yml build`
- Dev close/take down: `docker compose -f docker-compose.dev.yml down`
- Prod start: `docker compose up`
- Prod rebuild image: `docker compose build`
- Prod close/take down: `docker compose down`
- Inspect: `docker ps` # allowed without asking
- Inspect all docker (stop/running): `docker ps -a` # allowed without asking


## Docker Notes
`docker-compose.yml`:
- builds from `backend/dockerfile`
- publishes `8000:8000`
- loads backend settings from the repo root `.env`
- mounts `${USERPROFILE}/.aws:/root/.aws:ro`

`docker-compose.dev.yml`:
- builds from `backend/Dockerfile.dev`
- publishes `8000:8000`
- loads backend settings from the repo root `.env`
- mounts `${USERPROFILE}/.aws:/root/.aws:ro`
- bind-mounts `./backend:/app` for live backend edits, including the `app/` source package used by `uvicorn --reload`

## Runtime Notes
- `python-multipart` is required for uploads
- `bcrypt` and `PyJWT` support the temporary login flow
- the frontend assumes the backend is reachable at `http://localhost:8000/api` unless `VITE_API_BASE` is set
- CORS is enabled in the backend specifically for local frontend development

## Notes For Future Work
- `backend/run.txt` contains older example commands and sensitive-looking values, so treat it carefully
- Use `head_object` only when a user opens a file detail view for fuller metadata
- Rename should preserve the original extension even when the user omits it or types a different one
- Add a trash feature similar to Google Drive so deleted files can be restored later
- Keep folder browsing based on S3 prefixes and delimiters so deep nested paths continue to work
- Keep search and sort in the current folder view based on the existing `list_objects_v2` response unless the user explicitly asks for deeper metadata
- Large file renames may take longer, so the UI should make that delay clear during the rename flow
- Frontend logout currently clears the saved JWT locally, so add future backend logout or token revocation support if server-side sign-out is needed
- Refactor the frontend app UI into multiple files so auth, file browsing, and side-panel tools are easier to maintain

## Feature Tracking
- Keep this file updated whenever a feature is added, removed, or merged
- Record user-facing features in `README.md` so the repo history stays easy to follow
- If a branch changes user-facing behavior, update this document in the same pass
- Document features in simple user-facing language
- Remove technical detail from feature documentation
