import { useEffect, useMemo, useState } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api';

function normalizeKey(value) {
  return value.trim().replace(/^\/+/, '');
}

function displayPath(key) {
  if (!key) return '/';
  return key.startsWith('/') ? key : `/${key}`;
}

function formatBytes(value) {
  if (typeof value !== 'number' || Number.isNaN(value) || value < 0) {
    return 'Unknown size';
  }

  if (value === 0) {
    return '0 bytes';
  }

  const units = ['bytes', 'kB', 'MB', 'GB', 'TB', 'PB'];
  const unitIndex = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  const scaled = value / 1024 ** unitIndex;
  const digits = scaled >= 10 || unitIndex === 0 ? 0 : 1;

  return `${scaled.toFixed(digits)} ${units[unitIndex]}`;
}

function formatDateTime(value) {
  if (!value) return 'Unknown';

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return 'Unknown';
  }

  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);
}

async function readResponse(response) {
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return response.json();
  }
  return response.text();
}

async function api(path, options = {}) {
  const hasBody = options.body !== undefined && options.body !== null;
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...(hasBody && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
  });

  const payload = await readResponse(response);

  if (!response.ok) {
    const message = typeof payload === 'string' ? payload : payload?.detail || response.statusText;
    throw new Error(message || 'Request failed');
  }

  return payload;
}

export default function App() {
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [deleteKey, setDeleteKey] = useState('');
  const [deleting, setDeleting] = useState('');
  const [downloading, setDownloading] = useState('');

  const sortedFiles = useMemo(
    () => [...files].sort((a, b) => a.key.localeCompare(b.key)),
    [files],
  );

  async function loadFiles(isManualRefresh = false) {
    try {
      setError('');
      setSuccess('');
      if (isManualRefresh) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }
      const data = await api('/files');
      setFiles(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message || 'Could not load files');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    loadFiles();
  }, []);

  async function handleUpload(event) {
    event.preventDefault();
    if (!selectedFile) return;

    try {
      setError('');
      setSuccess('');
      setUploading(true);
      const formData = new FormData();
      formData.append('file', selectedFile);

      await api('/upload', {
        method: 'POST',
        body: formData,
      });

      setSelectedFile(null);
      event.currentTarget.reset();
      await loadFiles(true);
    } catch (err) {
      setError(err.message || 'Upload failed');
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(rawKey) {
    const normalized = normalizeKey(rawKey);
    if (!normalized) return;

    try {
      setError('');
      setSuccess('');
      setDeleting(normalized);
      await api(`/files/${encodeURIComponent(normalized)}`, {
        method: 'DELETE',
      });
      if (normalizeKey(deleteKey) === normalized) {
        setDeleteKey('');
      }
      await loadFiles(true);
    } catch (err) {
      setError(err.message || 'Delete failed');
    } finally {
      setDeleting('');
    }
  }

  async function handleDownload(rawKey) {
    const normalized = normalizeKey(rawKey);
    if (!normalized) return;

    try {
      setError('');
      setSuccess('');
      setDownloading(normalized);
      const data = await api(`/files/${encodeURIComponent(normalized)}/download`);
      if (!data?.url) {
        throw new Error('Download URL not found');
      }
      const link = document.createElement('a');
      link.href = data.url;
      link.rel = 'noopener';
      document.body.appendChild(link);
      link.click();
      link.remove();
      setSuccess(`Download started for ${displayPath(normalized)}`);
    } catch (err) {
      setError(err.message || 'Download failed');
    } finally {
      setDownloading('');
    }
  }

  return (
    <main className="app-shell">
      <section className="header-row">
        <div>
          <p className="eyebrow">Personal cloud storage</p>
          <h1>Files</h1>
          <p className="subtle">List, upload, and delete S3 objects from one simple screen.</p>
        </div>
        <button className="secondary-button" onClick={() => loadFiles(true)} disabled={refreshing || loading}>
          {refreshing ? 'Refreshing...' : 'Refresh'}
        </button>
      </section>

      <section className="panel">
        <form className="form-row" onSubmit={handleUpload}>
          <label className="field">
            <span>Upload file</span>
            <input
              type="file"
              onChange={(event) => setSelectedFile(event.target.files?.[0] || null)}
            />
          </label>
          <button className="primary-button" type="submit" disabled={!selectedFile || uploading}>
            {uploading ? 'Uploading...' : 'Upload'}
          </button>
        </form>
      </section>

      <section className="panel">
        <div className="section-head">
          <h2>Delete by path or name</h2>
        </div>
        <div className="form-row">
          <label className="field grow">
            <span>Object key</span>
            <input
              type="text"
              value={deleteKey}
              onChange={(event) => setDeleteKey(event.target.value)}
              placeholder="folder/file.txt"
            />
          </label>
          <button
            className="danger-button"
            onClick={() => handleDelete(deleteKey)}
            disabled={!normalizeKey(deleteKey) || deleting}
            type="button"
          >
            {deleting && normalizeKey(deleteKey) === deleting ? 'Deleting...' : 'Delete'}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="section-head">
          <h2>Objects</h2>
          <span className="count">{sortedFiles.length} items</span>
        </div>

        {error ? <div className="error-box">{error}</div> : null}
        {success ? <div className="success-box">{success}</div> : null}

        {loading ? (
          <div className="empty-state">Loading files...</div>
        ) : sortedFiles.length === 0 ? (
          <div className="empty-state">No files found.</div>
        ) : (
          <ul className="file-list">
            {sortedFiles.map((file) => (
              <li className="file-row" key={file.key}>
                <div className="file-meta">
                  <div className="file-name">{file.name || 'Unnamed file'}</div>
                  <div className="file-path">{file.path || displayPath(file.key)}</div>
                  <dl className="file-details">
                    <div className="file-detail">
                      <dt>Size</dt>
                      <dd>{formatBytes(file.size)}</dd>
                    </div>
                    <div className="file-detail">
                      <dt>Upload date</dt>
                      <dd>{formatDateTime(file.upload_date)}</dd>
                    </div>
                  </dl>
                </div>
                <div className="row-actions">
                  <button
                    className="secondary-button"
                    onClick={() => handleDownload(file.key)}
                    disabled={downloading === file.key}
                    type="button"
                  >
                    {downloading === file.key ? 'Preparing...' : 'Download'}
                  </button>
                  <button
                    className="ghost-button"
                    onClick={() => handleDelete(file.key)}
                    disabled={deleting === file.key}
                    type="button"
                  >
                    {deleting === file.key ? 'Deleting...' : 'Delete'}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
