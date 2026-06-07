# Personal Cloud Storage Agent Guide

## What This Project Is
This repo contains:
- a FastAPI backend that lists, uploads, and deletes files in one S3 bucket
- a React frontend that calls that backend directly from the browser

The frontend is run locally with Node/Vite. The backend is run with Docker.

## Current Structure
- Backend app: `backend/storage_backend.py`
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

The backend creates an S3 client with the normal AWS credential chain. In this repo, Docker mounts the local AWS credentials directory into the container read-only.

## Frontend Summary
- Stack: React with Vite
- Local dev port: `3000`
- Default backend base URL: `http://localhost:8000/api`

The frontend is not part of Docker. It talks to the backend directly over HTTP.

## API Surface
- `GET /api/` returns `{"status":"ok"}`
- `GET /api/whoami` returns the current AWS caller identity
- `GET /api/check_bucket` returns the configured bucket name
- `GET /api/files` returns file items with key, name, path, size, upload date, last modified, and file type
- `POST /api/upload` uploads one multipart file
- `GET /api/files/{key}/download` returns a short-lived presigned download URL
- `DELETE /api/files/{key}` deletes an object by key
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
- Before running `git push`, ask the user and list the exact CLI command.
- Every time a branch is switched or created, state the current branch afterward.
- Dev containers should rely on the bind mount from `docker-compose.dev.yml`.
- Keep generated cache and log files out of git.

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
- sets `S3_BUCKET=storage-602343785232-ap-southeast-1-an`
- sets `CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000`
- mounts `${USERPROFILE}/.aws:/root/.aws:ro`

`docker-compose.dev.yml`:
- builds from `backend/Dockerfile.dev`
- publishes `8000:8000`
- uses the same `S3_BUCKET`
- uses the same `CORS_ORIGINS`
- mounts `${USERPROFILE}/.aws:/root/.aws:ro`
- bind-mounts `./backend:/app` for live backend edits

## Runtime Notes
- `python-multipart` is required for uploads
- the frontend assumes the backend is reachable at `http://localhost:8000/api` unless `VITE_API_BASE` is set
- CORS is enabled in the backend specifically for local frontend development

## Notes For Future Work
- `backend/run.txt` contains older example commands and sensitive-looking values, so treat it carefully
- `GET /api/files` currently returns only `key` and `size`
- if richer metadata is needed, it must be added explicitly in the backend response

## Feature Tracking
- Keep this file updated whenever a feature is added, removed, or merged
- Record user-facing features in `README.md` so the repo history stays easy to follow
- If a branch changes user-facing behavior, update this document in the same pass
- Document features in simple user-facing language
- Remove technical detail from feature documentation
