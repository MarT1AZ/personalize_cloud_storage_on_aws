import { useEffect, useMemo, useState } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api';

function normalizeKey(value) {
  return value.trim().replace(/^\/+/, '');
}

function displayPath(key) {
  if (!key) return '/';
  return key.startsWith('/') ? key : `/${key}`;
}

function normalizePrefix(value) {
  const normalized = normalizeKey(value);
  if (!normalized) {
    return '';
  }
  return normalized.endsWith('/') ? normalized : `${normalized}/`;
}

function buildFolderPreview(prefix, rawName) {
  const normalizedPrefix = normalizePrefix(prefix);
  const normalizedName = normalizeKey(rawName).replace(/\/+$/, '');
  if (!normalizedName) {
    return normalizedPrefix;
  }
  return `${normalizedPrefix}${normalizedName}/`;
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

function sortItems(items, sortMode) {
  function compareAlphabet(left, right, direction = 'asc') {
    const leftValue = left.name || left.path || left.key;
    const rightValue = right.name || right.path || right.key;
    const result = leftValue.localeCompare(rightValue, undefined, { sensitivity: 'base' });
    return direction === 'desc' ? result * -1 : result;
  }

  function compareModified(left, right, direction = 'newest') {
    const leftTime = left.upload_date ? Date.parse(left.upload_date) : Number.NaN;
    const rightTime = right.upload_date ? Date.parse(right.upload_date) : Number.NaN;
    const leftHasTime = Number.isFinite(leftTime);
    const rightHasTime = Number.isFinite(rightTime);

    if (leftHasTime && rightHasTime) {
      if (leftTime !== rightTime) {
        return direction === 'oldest' ? leftTime - rightTime : rightTime - leftTime;
      }
      return compareAlphabet(left, right, 'asc');
    }

    if (leftHasTime) return -1;
    if (rightHasTime) return 1;
    return compareAlphabet(left, right, 'asc');
  }

  const nextItems = [...items];

  if (sortMode === 'alpha-desc') {
    nextItems.sort((left, right) => compareAlphabet(left, right, 'desc'));
  } else if (sortMode === 'modified-newest') {
    nextItems.sort((left, right) => compareModified(left, right, 'newest'));
  } else if (sortMode === 'modified-oldest') {
    nextItems.sort((left, right) => compareModified(left, right, 'oldest'));
  } else {
    nextItems.sort((left, right) => compareAlphabet(left, right, 'asc'));
  }

  return nextItems;
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
  const [folders, setFolders] = useState([]);
  const [files, setFiles] = useState([]);
  const [currentPrefix, setCurrentPrefix] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [sortMode, setSortMode] = useState('alpha-asc');
  const [groupMode, setGroupMode] = useState('folder-first');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [folderName, setFolderName] = useState('');
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [deleteKey, setDeleteKey] = useState('');
  const [deleteMode, setDeleteMode] = useState(false);
  const [deleteConfirmKey, setDeleteConfirmKey] = useState('');
  const [deleting, setDeleting] = useState('');
  const [downloading, setDownloading] = useState('');
  const [renameDrafts, setRenameDrafts] = useState({});
  const [renaming, setRenaming] = useState('');
  const [actionMenuKey, setActionMenuKey] = useState('');
  const [editingKey, setEditingKey] = useState('');
  const [renameNotices, setRenameNotices] = useState({});
  const [recentRenames, setRecentRenames] = useState({});
  const [recentDeletes, setRecentDeletes] = useState([]);

  const breadcrumbItems = useMemo(() => {
    const parts = currentPrefix.split('/').filter(Boolean);
    let runningPrefix = '';

    return [
      { label: 'Root', prefix: '' },
      ...parts.map((part) => {
        runningPrefix = `${runningPrefix}${part}/`;
        return {
          label: part,
          prefix: runningPrefix,
        };
      }),
    ];
  }, [currentPrefix]);

  const visibleItems = useMemo(() => {
    const keyword = searchQuery.trim().toLowerCase();
    const matchesKeyword = (item) => {
      if (!keyword) return true;

      const name = String(item.name || '').toLowerCase();
      return name.includes(keyword);
    };

    const filteredFolders = folders.filter(matchesKeyword);
    const filteredFiles = files.filter(matchesKeyword);

    const sortedFolders = sortItems(filteredFolders, sortMode);
    const sortedFiles = sortItems(filteredFiles, sortMode);

    return groupMode === 'file-first'
      ? [...sortedFiles, ...sortedFolders]
      : [...sortedFolders, ...sortedFiles];
  }, [files, folders, groupMode, searchQuery, sortMode]);

  const totalItemCount = folders.length + files.length;

  async function loadFiles(
    nextPrefix = currentPrefix,
    isManualRefresh = false,
    preserveRecentRenames = false,
    preserveRecentDeletes = false,
  ) {
    const normalizedPrefix = normalizePrefix(nextPrefix);

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
      const data = await api(`/files?prefix=${encodeURIComponent(normalizedPrefix)}`);
      setCurrentPrefix(data?.prefix || normalizedPrefix);
      setFolders(Array.isArray(data?.folders) ? data.folders : []);
      setFiles(Array.isArray(data?.files) ? data.files : []);
    } catch (err) {
      setError(err.message || 'Could not load files');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    loadFiles('');
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
      formData.append('prefix', currentPrefix);

      await api('/upload', {
        method: 'POST',
        body: formData,
      });

      setSelectedFile(null);
      event.currentTarget.reset();
      await loadFiles(currentPrefix, true);
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
      setDeleteConfirmKey('');
      await loadFiles(currentPrefix, true, true, true);
    } catch (err) {
      setDeleteConfirmKey('');
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

  async function handleCreateFolder(event) {
    event.preventDefault();
    if (!normalizeKey(folderName)) return;

    try {
      setError('');
      setSuccess('');
      setCreatingFolder(true);
      const data = await api('/folders', {
        method: 'POST',
        body: JSON.stringify({
          name: folderName,
          prefix: currentPrefix,
        }),
      });
      setFolderName('');
      setSuccess(`Created folder ${displayPath(data?.created_folder || buildFolderPreview(currentPrefix, folderName))}`);
      await loadFiles(currentPrefix, true, true, true);
    } catch (err) {
      setError(err.message || 'Folder creation failed');
    } finally {
      setCreatingFolder(false);
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
      await loadFiles(currentPrefix, true, true, true);
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
          <p className="subtle">Browse folders, open nested paths, and manage files from one simple screen.</p>
        </div>
        <div className="header-actions">
          <button
            className={deleteMode ? 'danger-button' : 'secondary-button'}
            onClick={() => {
              setDeleteMode((current) => !current);
              setDeleteConfirmKey('');
            }}
            type="button"
          >
            {deleteMode ? 'Exit Trash' : 'Trash'}
          </button>
          <button className="secondary-button" onClick={() => loadFiles(currentPrefix, true)} disabled={refreshing || loading}>
            {refreshing ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
      </section>

      <section className="workspace-grid">
        <div className="main-column">
          <section className="panel panel-main">
            <div className="section-head">
              <h2>Objects</h2>
              <span className="count">
                {visibleItems.length === totalItemCount ? `${totalItemCount} items` : `${visibleItems.length} of ${totalItemCount} items`}
              </span>
            </div>

            <div className="breadcrumbs" aria-label="Folder path">
              {breadcrumbItems.map((item) => (
                <button
                  key={item.prefix || 'root'}
                  className={item.prefix === currentPrefix ? 'breadcrumb-current' : 'breadcrumb-link'}
                  disabled={item.prefix === currentPrefix}
                  onClick={() => loadFiles(item.prefix, false, true, true)}
                  type="button"
                >
                  {item.label}
                </button>
              ))}
            </div>

            <div className="list-controls">
              <label className="field control-field">
                <span>Search</span>
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="Search this folder"
                />
              </label>
              <label className="field control-field">
                <span>Sort</span>
                <select value={sortMode} onChange={(event) => setSortMode(event.target.value)}>
                  <option value="alpha-asc">Alphabet (A to Z)</option>
                  <option value="alpha-desc">Alphabet (Z to A)</option>
                  <option value="modified-newest">Newest modified</option>
                  <option value="modified-oldest">Oldest modified</option>
                </select>
              </label>
              <label className="field control-field">
                <span>Group</span>
                <select value={groupMode} onChange={(event) => setGroupMode(event.target.value)}>
                  <option value="folder-first">Folder first</option>
                  <option value="file-first">File first</option>
                </select>
              </label>
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
            ) : totalItemCount === 0 ? (
              <div className="empty-state">No files found in this folder.</div>
            ) : visibleItems.length === 0 ? (
              <div className="empty-state">No matching items in this folder.</div>
            ) : (
              <ul className="file-list">
                {currentPrefix ? (
                  <li className="file-row folder-row folder-up-row">
                    <button
                      className="folder-open-button"
                      onClick={() => {
                        const parts = currentPrefix.split('/').filter(Boolean);
                        const parentPrefix = parts.length > 1 ? `${parts.slice(0, -1).join('/')}/` : '';
                        loadFiles(parentPrefix, false, true, true);
                      }}
                      type="button"
                    >
                      <span className="folder-icon" aria-hidden="true">📁</span>
                      <span className="folder-label">..</span>
                    </button>
                  </li>
                ) : null}

                {visibleItems.map((item) => {
                  if (item.kind === 'folder') {
                    return (
                      <li className="file-row folder-row" key={item.key}>
                        <div className="folder-row-shell">
                          <button
                            className="folder-open-button"
                            onClick={() => loadFiles(item.key, false, true, true)}
                            type="button"
                          >
                            <span className="folder-icon" aria-hidden="true">📁</span>
                            <span className="folder-label">{item.name}</span>
                            <span className="folder-path">{item.path}</span>
                          </button>
                          {deleteMode ? (
                            <div className="row-actions">
                              {deleteConfirmKey === item.key ? (
                                <>
                                  <button
                                    className="danger-button"
                                    onClick={() => handleDelete(item.key)}
                                    disabled={deleting === item.key}
                                    type="button"
                                  >
                                    {deleting === item.key ? 'Deleting...' : 'Confirm'}
                                  </button>
                                  <button
                                    className="ghost-button"
                                    onClick={() => setDeleteConfirmKey('')}
                                    type="button"
                                  >
                                    Cancel
                                  </button>
                                </>
                              ) : (
                                <button
                                  className="danger-button"
                                  onClick={() => setDeleteConfirmKey(item.key)}
                                  type="button"
                                >
                                  Delete
                                </button>
                              )}
                            </div>
                          ) : null}
                        </div>
                      </li>
                    );
                  }

                  return (
                    <li
                      className="file-row"
                      key={item.key}
                      data-file-key={item.key}
                      onContextMenu={(event) => {
                        event.preventDefault();
                        if (item.key.endsWith('/')) return;
                        setActionMenuKey((current) => (current === item.key ? '' : item.key));
                      }}
                    >
                      <div className="file-meta">
                        <div className="file-name-row">
                          <div className="file-name">{item.name || 'Unnamed file'}</div>
                          {recentRenames[item.key] ? (
                            <div className="rename-tag">
                              {recentRenames[item.key].oldName} {'>>'} {recentRenames[item.key].newName}
                            </div>
                          ) : null}
                        </div>
                        <div className="file-path">{item.path || displayPath(item.key)}</div>
                        <dl className="file-details">
                          <div className="file-detail">
                            <dt>Size</dt>
                            <dd>{formatBytes(item.size)}</dd>
                          </div>
                          <div className="file-detail">
                            <dt>Upload date</dt>
                            <dd>{formatDateTime(item.upload_date)}</dd>
                          </div>
                        </dl>
                        {!item.key.endsWith('/') && actionMenuKey === item.key ? (
                          <div className="action-menu">
                            <button
                              className="secondary-button"
                              onClick={() => {
                                setEditingKey(item.key);
                                setActionMenuKey('');
                              }}
                              type="button"
                            >
                              Rename
                            </button>
                          </div>
                        ) : null}
                        {!item.key.endsWith('/') && editingKey === item.key ? (
                          <div className="rename-box">
                            <label className="field grow">
                              <span>Rename file</span>
                              <input
                                type="text"
                                value={renameDrafts[item.key] || ''}
                                onChange={(event) => setRenameDrafts((current) => ({
                                  ...current,
                                  [item.key]: event.target.value,
                                }))}
                                placeholder="new-file-name"
                              />
                            </label>
                            <div className="rename-preview">
                              Final name: {displayPath(getRenamedKey(item.key, renameDrafts[item.key] || '')) || 'Enter a new name'}
                            </div>
                            {renameNotices[item.key] ? (
                              <div className={renameNotices[item.key].type === 'success' ? 'inline-success-box' : 'inline-error-box'}>
                                {renameNotices[item.key].message}
                              </div>
                            ) : null}
                            <div className="rename-actions">
                              <button
                                className="secondary-button"
                                onClick={() => handleRename(item)}
                                disabled={!getRenamedKey(item.key, renameDrafts[item.key] || '') || renaming === item.key}
                                type="button"
                              >
                                {renaming === item.key ? 'Renaming...' : 'Rename'}
                              </button>
                              <button
                                className="ghost-button"
                                onClick={() => {
                                  setEditingKey('');
                                  setRenameNotices((current) => ({
                                    ...current,
                                    [item.key]: null,
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
                        {deleteMode ? (
                          deleteConfirmKey === item.key ? (
                            <>
                              <button
                                className="danger-button"
                                onClick={() => handleDelete(item.key)}
                                disabled={deleting === item.key}
                                type="button"
                              >
                                {deleting === item.key ? 'Deleting...' : 'Confirm'}
                              </button>
                              <button
                                className="ghost-button"
                                onClick={() => setDeleteConfirmKey('')}
                                type="button"
                              >
                                Cancel
                              </button>
                            </>
                          ) : (
                            <button
                              className="danger-button"
                              onClick={() => setDeleteConfirmKey(item.key)}
                              type="button"
                            >
                              Delete
                            </button>
                          )
                        ) : null}
                        {!item.key.endsWith('/') ? (
                          <button
                            className="ghost-button"
                            onClick={() => setActionMenuKey((current) => (current === item.key ? '' : item.key))}
                            type="button"
                          >
                            Actions
                          </button>
                        ) : null}
                        <button
                          className="secondary-button"
                          onClick={() => handleDownload(item.key)}
                          disabled={downloading === item.key}
                          type="button"
                        >
                          {downloading === item.key ? 'Preparing...' : 'Download'}
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </div>

        <aside className="side-column">
          <section className="panel side-panel">
            <div className="section-head">
              <h2>Create folder</h2>
            </div>
            <form className="stack-form" onSubmit={handleCreateFolder}>
              <label className="field">
                <span>Folder name</span>
                <input
                  type="text"
                  value={folderName}
                  onChange={(event) => setFolderName(event.target.value)}
                  placeholder="new-folder"
                />
              </label>
              <div className="helper-text">
                Final folder: {displayPath(buildFolderPreview(currentPrefix, folderName)) || '/'}
              </div>
              <button className="primary-button" type="submit" disabled={!normalizeKey(folderName) || creatingFolder}>
                {creatingFolder ? 'Creating...' : 'Create folder'}
              </button>
            </form>
          </section>

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
              {currentPrefix ? (
                <div className="helper-text">Current folder: {displayPath(currentPrefix)}</div>
              ) : (
                <div className="helper-text">Current folder: /</div>
              )}
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
