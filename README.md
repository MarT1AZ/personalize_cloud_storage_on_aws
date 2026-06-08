# Personal Cloud Storage

Simple personal cloud storage app with a React frontend and a FastAPI backend backed by one S3 bucket.

The backend source now lives under `backend/app/` so the dev container can bind-mount the backend folder and reload code changes from a dedicated source package.

## Current Features
- Browse folders and files
- Create folders
- Open nested folders
- Show an absolute path for each item
- Show file name, size, and upload date for each item
- Rename a file
- Keep the original file extension during rename
- Enable trash mode to delete files and folders from the list
- Delete by path
- Delete on item card
- Show a recent deletion alert
- Upload file
