import { useEffect, useMemo, useState } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api';

function normalizeKey(value) {
  return value.trim().replace(/^\/+/, '');
}

function displayPath(key) {
  if (!key) return '/';
  return key.startsWith('/') ? key : `/${key}`;
}

function getFileExtension(key) {
  const name = key.split('/').pop() || '';
  const dotIndex = name.lastIndexOf('.');
  if (dotIndex <= 0) {
    return '';
  }
  return name.slice(dotIndex);
}

function getRenamedKey(key, rawName) {
  const normalizedName = normalizeKey(rawName);
  if (!normalizedName) {
    return '';
  }

  const extension = getFileExtension(key);
  const finalName = !extension || normalizedName.endsWith(extension) ? normalizedName : `${normalizedName}${extension}`;
  const slashIndex = key.lastIndexOf('/');
  const parentPath = slashIndex >= 0 ? key.slice(0, slashIndex + 1) : '';

  return `${parentPath}${finalName}`;
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
  const [renameDrafts, setRenameDrafts] = useState({});
  const [renaming, setRenaming] = useState('');
  const [actionMenuKey, setActionMenuKey] = useState('');
  const [editingKey, setEditingKey] = useState('');
  const [renameNotices, setRenameNotices] = useState({});
  const [recentRenames, setRecentRenames] = useState({});
  const [recentDeletes, setRecentDeletes] = useState([]);

  const sortedFiles = useMemo(
    () => [...files].sort((a, b) => a.key.localeCompare(b.key)),
    [files],
  );

  async function loadFiles(isManualRefresh = false, preserveRecentRenames = false, preserveRecentDeletes = false) {
    try {
      setError('');
      setSuccess('');
      if (isManualRefresh && !preserveRecentRenames) {
        setRecentRenames({});
      }
      if (isManualRefresh && !preserveRecentDeletes) {
        setRecentDeletes([]);
      }
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

  useEffect(() => {
    function handlePointerDown(event) {
      const row = event.target.closest('[data-file-key]');
      const clickedKey = row?.getAttribute('data-file-key') || '';

      if (actionMenuKey && clickedKey !== actionMenuKey) {
        setActionMenuKey('');
      }
    }

    window.addEventListener('pointerdown', handlePointerDown);
    return () => window.removeEventListener('pointerdown', handlePointerDown);
  }, [actionMenuKey]);

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
      setRecentDeletes((current) => {
        const next = [displayPath(normalized), ...current.filter((item) => item !== displayPath(normalized))];
        return next.slice(0, 5);
      });
      await loadFiles(true, true, true);
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

  async function handleRename(file) {
    const draft = renameDrafts[file.key] || '';
    const finalKey = getRenamedKey(file.key, draft);
    if (!finalKey) return;

    try {
      setError('');
      setSuccess('');
      setRenaming(file.key);
      setRenameNotices((current) => ({
        ...current,
        [file.key]: null,
      }));
      await api(`/files/${encodeURIComponent(file.key)}/rename`, {
        method: 'POST',
        body: JSON.stringify({
          new_name: draft,
        }),
      });
      setRenameDrafts((current) => ({
        ...current,
        [file.key]: '',
      }));
      setEditingKey('');
      setActionMenuKey('');
      setRecentRenames((current) => {
        const next = { ...current };
        delete next[file.key];
        next[finalKey] = {
          oldName: file.name || file.key,
          newName: finalKey.split('/').pop() || finalKey,
        };
        return next;
      });
      setRenameNotices((current) => ({
        ...current,
        [file.key]: {
          type: 'success',
          message: `Renamed to ${displayPath(finalKey)}`,
        },
      }));
      await loadFiles(true, true, true);
    } catch (err) {
      setRenameNotices((current) => ({
        ...current,
        [file.key]: {
          type: 'error',
          message: err.message || 'Rename failed',
        },
      }));
    } finally {
      setRenaming('');
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

      <section className="workspace-grid">
        <div className="main-column">
          <section className="panel panel-main">
            <div className="section-head">
              <h2>Objects</h2>
              <span className="count">{sortedFiles.length} items</span>
            </div>

            {error ? <div className="error-box">{error}</div> : null}
            {success ? <div className="success-box">{success}</div> : null}
            {recentDeletes.length > 0 ? (
              <div className="delete-history-box">
                <div className="delete-history-title">Recently deleted</div>
                <div className="delete-history-list">
                  {recentDeletes.map((item) => (
                    <div className="delete-tag" key={item}>{item}</div>
                  ))}
                </div>
              </div>
            ) : null}

            {loading ? (
              <div className="empty-state">Loading files...</div>
            ) : sortedFiles.length === 0 ? (
              <div className="empty-state">No files found.</div>
            ) : (
              <ul className="file-list">
                {sortedFiles.map((file) => (
                  <li
                    className="file-row"
                    key={file.key}
                    data-file-key={file.key}
                    onContextMenu={(event) => {
                      event.preventDefault();
                      if (file.key.endsWith('/')) return;
                      setActionMenuKey((current) => (current === file.key ? '' : file.key));
                    }}
                  >
                    <div className="file-meta">
                      <div className="file-name-row">
                        <div className="file-name">{file.name || 'Unnamed file'}</div>
                        {recentRenames[file.key] ? (
                          <div className="rename-tag">
                            {recentRenames[file.key].oldName} {'>>'} {recentRenames[file.key].newName}
                          </div>
                        ) : null}
                      </div>
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
                      {!file.key.endsWith('/') && actionMenuKey === file.key ? (
                        <div className="action-menu">
                          <button
                            className="secondary-button"
                            onClick={() => {
                              setEditingKey(file.key);
                              setActionMenuKey('');
                            }}
                            type="button"
                          >
                            Rename
                          </button>
                        </div>
                      ) : null}
                      {!file.key.endsWith('/') && editingKey === file.key ? (
                        <div className="rename-box">
                          <label className="field grow">
                            <span>Rename file</span>
                            <input
                              type="text"
                              value={renameDrafts[file.key] || ''}
                              onChange={(event) => setRenameDrafts((current) => ({
                                ...current,
                                [file.key]: event.target.value,
                              }))}
                              placeholder="new-file-name"
                            />
                          </label>
                          <div className="rename-preview">
                            Final name: {displayPath(getRenamedKey(file.key, renameDrafts[file.key] || '')) || 'Enter a new name'}
                          </div>
                          {renameNotices[file.key] ? (
                            <div className={renameNotices[file.key].type === 'success' ? 'inline-success-box' : 'inline-error-box'}>
                              {renameNotices[file.key].message}
                            </div>
                          ) : null}
                          <div className="rename-actions">
                            <button
                              className="secondary-button"
                              onClick={() => handleRename(file)}
                              disabled={!getRenamedKey(file.key, renameDrafts[file.key] || '') || renaming === file.key}
                              type="button"
                            >
                              {renaming === file.key ? 'Renaming...' : 'Rename'}
                            </button>
                            <button
                              className="ghost-button"
                              onClick={() => {
                                setEditingKey('');
                                setRenameNotices((current) => ({
                                  ...current,
                                  [file.key]: null,
                                }));
                              }}
                              type="button"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </div>
                    <div className="row-actions">
                      {!file.key.endsWith('/') ? (
                        <button
                          className="ghost-button"
                          onClick={() => setActionMenuKey((current) => (current === file.key ? '' : file.key))}
                          type="button"
                        >
                          Actions
                        </button>
                      ) : null}
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
        </div>

        <aside className="side-column">
          <section className="panel side-panel">
            <div className="section-head">
              <h2>Upload</h2>
            </div>
            <form className="stack-form" onSubmit={handleUpload}>
              <label className="field">
                <span>Select file</span>
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

          <section className="panel side-panel">
            <div className="section-head">
              <h2>Delete by path</h2>
            </div>
            <div className="stack-form">
              <label className="field">
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

          <section className="panel side-panel panel-muted">
            <div className="section-head">
              <h2>Coming Next</h2>
            </div>
            <p className="subtle panel-note">This side area is reserved for future tools like trash, filters, and richer file details.</p>
          </section>
        </aside>
      </section>
    </main>
  );
}
