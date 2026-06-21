# Personal Cloud Storage Agent Guide

## What This Project Is
This repo contains:
- a FastAPI backend that lists, uploads, and deletes files in one S3 bucket
- a React frontend served by Nginx, with `/api/...` proxied to the backend container

The app is run with Docker Compose.

## Current Structure
- Backend app: `backend/app/storage_backend.py`
- Backend config module: `backend/app/config.py`
- Backend storage service: `backend/app/services/object_storage_service.py`
- Backend production image: `backend/dockerfile`
- Backend ops image: `scripts/dockerfile`
- Backend dependencies: `backend/requirements.txt`
- Frontend app: `frontend/src/App.jsx`
- Frontend image: `frontend/dockerfile`
- Frontend Nginx config: `frontend/nginx/default.conf`
- Frontend entry page: `frontend/index.html`
- Frontend package config: `frontend/package.json`
- Docker Compose: `docker-compose.yml`

## Backend Summary
- Framework: FastAPI
- AWS SDK: `boto3`
- Server: `uvicorn`
- Port: `8000`

The backend creates an S3 client with the normal AWS credential chain. In this repo, Docker mounts the local AWS credentials directory into the container read-only.
The route file should stay focused on request models and API handlers, while shared S3 behavior lives in the service layer.
The config module should load settings from `backend/pcs_backend_production.env`, `backend/.env`, or `backend/template.env` and expose them as Python settings values instead of direct `os.environ[...]` reads in route code.

## Frontend Summary
- Stack: React with Vite
- Browser port: `3000`
- API base URL: `/api`

The frontend is built into an Nginx image. The browser talks to Nginx, and Nginx proxies `/api/...` requests to the backend container over the Docker bridge network.

## API Surface
- `GET /api/` returns `{"status":"ok"}`
- `POST /api/auth/login` returns a bearer token plus the temporary user profile
- `GET /api/auth/me` returns the current authenticated user from the bearer token
- `GET /api/whoami` returns the current AWS caller identity
- `GET /api/check_bucket` returns the current authenticated user's bucket info
- `GET /api/files?folder_id=...` returns the current folder id plus immediate child folders and files
- `GET /api/trash` returns trashed files for the current user
- `POST /api/folders` creates a folder inside the provided parent folder
- `POST /api/upload` uploads one multipart file, optionally into the provided folder
- `GET /api/files/{file_id}/download` returns a short-lived presigned download URL
- `POST /api/files/{file_id}/rename` updates a file name while preserving its extension
- `DELETE /api/files/{file_id}` soft-deletes a file, or soft-deletes an empty folder
- `POST /api/trash/soft-delete` soft-deletes multiple selected files
- `POST /api/trash/restore` restores selected trashed files
- `POST /api/trash/delete` permanently deletes selected trashed files
- `DELETE /api/delete?file_id=...` is an alias for delete by query string

## How To Run
App run:
1. From the repo root, run `docker compose up --build`
2. Open `http://localhost:3000` for the app
3. Open `http://localhost:8000/docs` for Swagger

## Git workflow & permission
- Git CLI commands that change repository state require user permission.
- Read-only Git CLI commands that do not change code or repository state, such as `git status`, `git log`, and `git diff`, can be run without asking first.
- Before running `git add` and `git commit`, list the exact CLI commands and the commit message, and make sure the message matches the actual work done.
- Use `git commit -m "Title" -m "Detailed description"` so commits include both a title and a description.
- Before running `git push`, ask the user and list the exact CLI command.
- Every time a branch is switched or created, state the current branch afterward.
- Keep generated cache and log files out of git.

## S3 and API Permission
- Ask for user permission before any S3 read, S3 write, or API call that reads from or writes to S3
- Do not use the app API or direct AWS calls for live verification without user approval
- Treat upload, delete, rename, download-link generation, and list checks against the real bucket as permission-gated actions

## Docker workflow & permission
- Start: `docker compose up`
- Rebuild image: `docker compose build`
- Close/take down: `docker compose down`
- Inspect: `docker ps` # allowed without asking
- Inspect all docker (stop/running): `docker ps -a` # allowed without asking


## Docker Notes
`docker-compose.yml`:
- builds from `backend/dockerfile`
- builds the frontend from `frontend/dockerfile`
- publishes `3000:80` for the frontend
- backend stays on the internal Docker network
- loads backend settings from `backend/pcs_backend_production.env`
- mounts `${USERPROFILE}/.aws:/root/.aws:ro`
- uses a bridge network so Nginx can proxy `/api/...` to `backend:8000`

## Testing Scripts
`scripts/dockerfile`:
- includes `backend/app` and `scripts/`
- is intended for maintenance commands such as storage validation scripts
- keeps the main runtime image slim by leaving scripts out of `backend/dockerfile`
- build command: `docker build -f scripts/dockerfile -t pcs_backend_ops .`
- run DB -> S3 validation:
  `docker run --rm --env-file backend/pcs_backend_production.env -v ${USERPROFILE}/.aws:/root/.aws:ro pcs_backend_ops python /app/scripts/check_storage_consistency.py --bucket <bucket-name>`
- run S3 -> DB validation:
  `docker run --rm --env-file backend/pcs_backend_production.env -v ${USERPROFILE}/.aws:/root/.aws:ro pcs_backend_ops python /app/scripts/check_s3_source_of_truth.py --bucket <bucket-name>`

## Runtime Notes
- `python-multipart` is required for uploads
- `bcrypt` and `PyJWT` support the temporary login flow
- the frontend assumes the API is reachable at `/api`

## Notes For Future Work


## Feature Tracking
- Keep this file updated whenever a feature is added, removed, or merged
- Record user-facing features in `README.md` so the repo history stays easy to follow
- If a branch changes user-facing behavior, update this document in the same pass
- Document features in simple user-facing language
- Remove technical detail from feature documentation
