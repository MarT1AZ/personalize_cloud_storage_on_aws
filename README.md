# Personal Cloud Storage

Simple personal cloud storage app with a React frontend and a FastAPI backend backed by one S3 bucket.

The backend source now lives under `backend/app/` so the dev container can bind-mount the backend folder and reload code changes from a dedicated source package.
Backend settings now come from a root `.env` file, with `.env.template` provided as the starting point.

## Current Features
Authentication
- Log in with username and password
- Stay signed in after page reload with a saved JWT token

Browsing
- Browse folders and files
- Open nested folders
- Go up to the parent folder from a nested path
- Show an absolute path for each item
- Search items in the current folder by keyword
- Sort items by alphabet or last modified time
- Switch between folder-first and file-first ordering

Folders
- Create folders
- Prevent creating a folder when an active folder with the same name already exists
- Soft-delete empty folders

Files
- Upload file
- Upload duplicate active file names as copy variants automatically
- Show file name, size, and upload date for each item
- Download a file from the list
- Rename a file
- Keep the original file extension during rename

Trash
- Move one file to trash from the list
- Move multiple files to trash from the current folder
- View trashed files
- Restore trashed files
- Permanently delete trashed files
- Show recent deletion history in the UI
