import { useEffect, useMemo, useState } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api';
const AUTH_TOKEN_KEY = 'pcs_auth_token';

function normalizeKey(value) {
  return String(value || '').trim().replace(/^\/+/, '');
}

function normalizeId(value) {
  return String(value || '').trim();
}

function displayPath(path) {
  if (!path) return '/';
  return path.startsWith('/') ? path : `/${path}`;
}

function buildFolderPreview(currentPath, rawName) {
  const normalizedName = normalizeKey(rawName).replace(/\/+$/, '');
  if (!normalizedName) {
    return displayPath(currentPath || '/');
  }

  const basePath = displayPath(currentPath || '/');
  return basePath === '/' ? `/${normalizedName}/` : `${basePath}${normalizedName}/`;
}

function buildRenamedObjectName(rawName, fileExtension = '') {
  const normalizedName = normalizeKey(rawName);
  if (!normalizedName) {
    return '';
  }

  if (fileExtension && !normalizedName.endsWith(fileExtension)) {
    return `${normalizedName}${fileExtension}`;
  }

  return normalizedName;
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
    const leftValue = left.name || left.path || left.file_id;
    const rightValue = right.name || right.path || right.file_id;
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
  const { authToken = '', ...requestOptions } = options;
  const hasBody = requestOptions.body !== undefined && requestOptions.body !== null;
  const response = await fetch(`${API_BASE}${path}`, {
    ...requestOptions,
    headers: {
      ...(hasBody && !(requestOptions.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
      ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
      ...(requestOptions.headers || {}),
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
  const [authToken, setAuthToken] = useState('');
  const [authUser, setAuthUser] = useState(null);
  const [authChecking, setAuthChecking] = useState(true);
  const [loginUsername, setLoginUsername] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [loginSubmitting, setLoginSubmitting] = useState(false);
  const [authError, setAuthError] = useState('');

  const [folders, setFolders] = useState([]);
  const [files, setFiles] = useState([]);
  const [trashedFiles, setTrashedFiles] = useState([]);
  const [currentFolderId, setCurrentFolderId] = useState('');
  const [currentPath, setCurrentPath] = useState('/');
  const [breadcrumbItems, setBreadcrumbItems] = useState([{ label: 'Root', folder_id: '' }]);
  const [viewMode, setViewMode] = useState('files');
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
  const [deleteObjectId, setDeleteObjectId] = useState('');
  const [deleteMode, setDeleteMode] = useState(false);
  const [deleteConfirmKey, setDeleteConfirmKey] = useState('');
  const [deleting, setDeleting] = useState('');
  const [trashAction, setTrashAction] = useState('');
  const [downloading, setDownloading] = useState('');
  const [renameDrafts, setRenameDrafts] = useState({});
  const [renaming, setRenaming] = useState('');
  const [actionMenuKey, setActionMenuKey] = useState('');
  const [editingKey, setEditingKey] = useState('');
  const [renameNotices, setRenameNotices] = useState({});
  const [recentRenames, setRecentRenames] = useState({});
  const [recentDeletes, setRecentDeletes] = useState([]);
  const [selectedTrashIds, setSelectedTrashIds] = useState({});

  const visibleItems = useMemo(() => {
    const keyword = searchQuery.trim().toLowerCase();
    const matchesKeyword = (item) => {
      if (!keyword) return true;
      return String(item.name || '').toLowerCase().includes(keyword);
    };

    if (viewMode === 'trash') {
      return sortItems(trashedFiles.filter(matchesKeyword), sortMode);
    }

    const filteredFolders = folders.filter(matchesKeyword);
    const filteredFiles = files.filter(matchesKeyword);
    const sortedFolders = sortItems(filteredFolders, sortMode);
    const sortedFiles = sortItems(filteredFiles, sortMode);

    return groupMode === 'file-first'
      ? [...sortedFiles, ...sortedFolders]
      : [...sortedFolders, ...sortedFiles];
  }, [files, folders, groupMode, searchQuery, sortMode, trashedFiles, viewMode]);

  const totalItemCount = viewMode === 'trash' ? trashedFiles.length : folders.length + files.length;
  const selectedTrashCount = Object.values(selectedTrashIds).filter(Boolean).length;

  function clearWorkspaceState() {
    setFolders([]);
    setFiles([]);
    setTrashedFiles([]);
    setCurrentFolderId('');
    setCurrentPath('/');
    setBreadcrumbItems([{ label: 'Root', folder_id: '' }]);
    setViewMode('files');
    setSearchQuery('');
    setSortMode('alpha-asc');
    setGroupMode('folder-first');
    setLoading(true);
    setRefreshing(false);
    setError('');
    setSuccess('');
    setSelectedFile(null);
    setUploading(false);
    setFolderName('');
    setCreatingFolder(false);
    setDeleteObjectId('');
    setDeleteMode(false);
    setDeleteConfirmKey('');
    setDeleting('');
    setTrashAction('');
    setDownloading('');
    setRenameDrafts({});
    setRenaming('');
    setActionMenuKey('');
    setEditingKey('');
    setRenameNotices({});
    setRecentRenames({});
    setRecentDeletes([]);
    setSelectedTrashIds({});
  }

  function handleLogout() {
    localStorage.removeItem(AUTH_TOKEN_KEY);
    setAuthToken('');
    setAuthUser(null);
    setAuthError('');
    clearWorkspaceState();
  }

  async function loadFiles(nextFolderId = currentFolderId, isManualRefresh = false, preserveRecentRenames = false, preserveRecentDeletes = false) {
    const normalizedFolderId = normalizeId(nextFolderId);

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

      const suffix = normalizedFolderId ? `?folder_id=${encodeURIComponent(normalizedFolderId)}` : '';
      const data = await api(`/files${suffix}`, { authToken });
      setCurrentFolderId(data?.current_folder_id || '');
      setCurrentPath(displayPath(data?.current_path || '/'));
      setBreadcrumbItems(Array.isArray(data?.breadcrumbs) && data.breadcrumbs.length > 0 ? data.breadcrumbs : [{ label: 'Root', folder_id: '' }]);
      setFolders(Array.isArray(data?.folders) ? data.folders : []);
      setFiles(Array.isArray(data?.files) ? data.files : []);
      setTrashedFiles([]);
      setSelectedTrashIds({});
      setDeleteMode(false);
      setViewMode('files');
    } catch (err) {
      if (err.message === 'Invalid or expired token') {
        handleLogout();
        setAuthError('Your session expired. Please log in again.');
      } else {
        setError(err.message || 'Could not load files');
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  async function loadTrash(isManualRefresh = false) {
    try {
      setError('');
      setSuccess('');
      if (isManualRefresh) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }

      const data = await api('/trash', { authToken });
      setFolders([]);
      setFiles([]);
      setCurrentFolderId('');
      setCurrentPath('/trash');
      setBreadcrumbItems([{ label: 'Trash', folder_id: '' }]);
      setTrashedFiles(Array.isArray(data?.files) ? data.files : []);
      setSelectedTrashIds({});
      setDeleteMode(false);
      setDeleteConfirmKey('');
      setViewMode('trash');
    } catch (err) {
      if (err.message === 'Invalid or expired token') {
        handleLogout();
        setAuthError('Your session expired. Please log in again.');
      } else {
        setError(err.message || 'Could not load files');
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    async function restoreSession() {
      const storedToken = localStorage.getItem(AUTH_TOKEN_KEY) || '';
      if (!storedToken) {
        setAuthChecking(false);
        setLoading(false);
        return;
      }

      try {
        const user = await api('/auth/me', { authToken: storedToken });
        setAuthToken(storedToken);
        setAuthUser(user);
      } catch {
        localStorage.removeItem(AUTH_TOKEN_KEY);
        setAuthToken('');
        setAuthUser(null);
        setAuthError('Please log in to continue.');
      } finally {
        setAuthChecking(false);
      }
    }

    restoreSession();
  }, []);

  useEffect(() => {
    if (authUser && authToken) {
      loadFiles('');
    }
  }, [authUser, authToken]);

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

  async function handleLogin(event) {
    event.preventDefault();
    try {
      setAuthError('');
      setLoginSubmitting(true);
      const data = await api('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username: loginUsername, password: loginPassword }),
      });
      localStorage.setItem(AUTH_TOKEN_KEY, data.access_token);
      setAuthToken(data.access_token);
      setAuthUser(data.user);
      setLoginPassword('');
      clearWorkspaceState();
    } catch (err) {
      setAuthError(err.message || 'Invalid username or password');
    } finally {
      setLoginSubmitting(false);
    }
  }

  async function handleUpload(event) {
    event.preventDefault();
    if (!selectedFile) return;

    try {
      setError('');
      setSuccess('');
      setUploading(true);
      const formData = new FormData();
      formData.append('file', selectedFile);
      formData.append('folder_id', currentFolderId);

      await api('/upload', {
        method: 'POST',
        body: formData,
        authToken,
      });

      setSelectedFile(null);
      event.currentTarget.reset();
      await loadFiles(currentFolderId, true);
    } catch (err) {
      setError(err.message || 'Upload failed');
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(rawObjectId) {
    const objectId = normalizeId(rawObjectId);
    if (!objectId) return;

    try {
      setError('');
      setSuccess('');
      setDeleting(objectId);
      const item = [...folders, ...files].find((entry) => entry.file_id === objectId);
      await api(`/files/${encodeURIComponent(objectId)}`, { method: 'DELETE', authToken });
      if (normalizeId(deleteObjectId) === objectId) {
        setDeleteObjectId('');
      }
      if (item?.path) {
        setRecentDeletes((current) => {
          const next = [item.path, ...current.filter((entry) => entry !== item.path)];
          return next.slice(0, 5);
        });
      }
      setDeleteConfirmKey('');
      await loadFiles(currentFolderId, true, true, true);
    } catch (err) {
      setDeleteConfirmKey('');
      setError(err.message || 'Delete failed');
    } finally {
      setDeleting('');
    }
  }

  async function handleRestoreTrash() {
    const objectIds = Object.entries(selectedTrashIds)
      .filter(([, isSelected]) => isSelected)
      .map(([objectId]) => objectId);

    if (objectIds.length === 0) return;

    try {
      setError('');
      setSuccess('');
      setTrashAction('restore');
      const data = await api('/trash/restore', {
        method: 'POST',
        body: JSON.stringify({ file_ids: objectIds }),
        authToken,
      });
      setSelectedTrashIds({});
      setDeleteConfirmKey('');
      const restoredCount = Array.isArray(data?.restored) ? data.restored.length : objectIds.length;
      setSuccess(`Restored ${restoredCount} file${restoredCount === 1 ? '' : 's'}.`);
      await loadTrash(true);
    } catch (err) {
      setError(err.message || 'Restore failed');
    } finally {
      setTrashAction('');
    }
  }

  async function handleDeleteTrash() {
    const objectIds = Object.entries(selectedTrashIds)
      .filter(([, isSelected]) => isSelected)
      .map(([objectId]) => objectId);

    if (objectIds.length === 0) return;

    try {
      setError('');
      setSuccess('');
      setTrashAction('delete');
      const data = await api('/trash/delete', {
        method: 'POST',
        body: JSON.stringify({ file_ids: objectIds }),
        authToken,
      });
      setSelectedTrashIds({});
      setDeleteConfirmKey('');
      const deletedCount = Array.isArray(data?.deleted) ? data.deleted.length : objectIds.length;
      setSuccess(`Deleted ${deletedCount} file${deletedCount === 1 ? '' : 's'} forever.`);
      await loadTrash(true);
    } catch (err) {
      setError(err.message || 'Permanent delete failed');
    } finally {
      setTrashAction('');
    }
  }

  async function handleDownload(rawObjectId) {
    const objectId = normalizeId(rawObjectId);
    if (!objectId) return;

    try {
      setError('');
      setSuccess('');
      setDownloading(objectId);
      const data = await api(`/files/${encodeURIComponent(objectId)}/download`, { authToken });
      if (!data?.url) {
        throw new Error('Download URL not found');
      }
      const item = [...folders, ...files].find((entry) => entry.file_id === objectId);
      const link = document.createElement('a');
      link.href = data.url;
      link.rel = 'noopener';
      document.body.appendChild(link);
      link.click();
      link.remove();
      setSuccess(`Download started for ${item?.name || objectId}`);
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
        body: JSON.stringify({ name: folderName, parent_id: currentFolderId }),
        authToken,
      });
      setFolderName('');
      setSuccess(`Created folder ${data?.created_folder_name || folderName}`);
      await loadFiles(currentFolderId, true, true, true);
    } catch (err) {
      setError(err.message || 'Folder creation failed');
    } finally {
      setCreatingFolder(false);
    }
  }

  async function handleRename(file) {
    const draft = renameDrafts[file.file_id] || '';
    const finalName = buildRenamedObjectName(draft, file.file_extension || '');
    if (!finalName) return;

    try {
      setError('');
      setSuccess('');
      setRenaming(file.file_id);
      setRenameNotices((current) => ({ ...current, [file.file_id]: null }));
      const data = await api(`/files/${encodeURIComponent(file.file_id)}/rename`, {
        method: 'POST',
        body: JSON.stringify({ new_name: draft }),
        authToken,
      });
      setRenameDrafts((current) => ({ ...current, [file.file_id]: '' }));
      setEditingKey('');
      setActionMenuKey('');
      setRecentRenames((current) => ({
        ...current,
        [file.file_id]: {
          oldName: file.name || file.file_id,
          newName: data?.renamed_name || finalName,
        },
      }));
      setRenameNotices((current) => ({
        ...current,
        [file.file_id]: {
          type: 'success',
          message: `Renamed to ${data?.renamed_name || finalName}`,
        },
      }));
      await loadFiles(currentFolderId, true, true, true);
    } catch (err) {
      setRenameNotices((current) => ({
        ...current,
        [file.file_id]: {
          type: 'error',
          message: err.message || 'Rename failed',
        },
      }));
    } finally {
      setRenaming('');
    }
  }

  function handleOpenFolder(folderId) {
    loadFiles(folderId, false, true, true);
  }

  function handleOpenParent() {
    const currentIndex = breadcrumbItems.findIndex((item) => item.folder_id === currentFolderId);
    const parentFolderId = currentIndex > 0 ? breadcrumbItems[currentIndex - 1].folder_id : '';
    loadFiles(parentFolderId, false, true, true);
  }

  function toggleTrashSelection(objectId) {
    setSelectedTrashIds((current) => ({
      ...current,
      [objectId]: !current[objectId],
    }));
  }

  function toggleSelectAllTrash() {
    if (visibleItems.length === 0) return;
    const shouldSelectAll = visibleItems.some((item) => !selectedTrashIds[item.file_id]);
    const nextSelection = {};

    visibleItems.forEach((item) => {
      nextSelection[item.file_id] = shouldSelectAll;
    });

    setSelectedTrashIds(nextSelection);
  }

  if (authChecking) {
    return (
      <main className="login-shell">
        <section className="panel login-card">
          <p className="eyebrow">Personal cloud storage</p>
          <h1>Checking session</h1>
          <p className="subtle">Please wait while we restore your access.</p>
        </section>
      </main>
    );
  }

  if (!authUser || !authToken) {
    return (
      <main className="login-shell">
        <section className="panel login-card">
          <p className="eyebrow">Personal cloud storage</p>
          <h1>Log in</h1>
          <p className="subtle">Use your username and password to open the file manager.</p>
          {authError ? <div className="error-box">{authError}</div> : null}
          <form className="stack-form" onSubmit={handleLogin}>
            <label className="field">
              <span>Username</span>
              <input type="text" value={loginUsername} onChange={(event) => setLoginUsername(event.target.value)} autoComplete="username" placeholder="marz" />
            </label>
            <label className="field">
              <span>Password</span>
              <input type="password" value={loginPassword} onChange={(event) => setLoginPassword(event.target.value)} autoComplete="current-password" placeholder="Enter password" />
            </label>
            <button className="primary-button" type="submit" disabled={!loginUsername.trim() || !loginPassword || loginSubmitting}>
              {loginSubmitting ? 'Logging in...' : 'Log in'}
            </button>
          </form>
        </section>
      </main>
    );
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
          <div className="session-badge">Signed in as {authUser.username}</div>
          <button
            className={deleteMode ? 'danger-button' : 'secondary-button'}
            onClick={() => {
              if (viewMode === 'trash') {
                return;
              }
              setDeleteConfirmKey('');
              setDeleteMode((current) => !current);
            }}
            disabled={viewMode === 'trash'}
            type="button"
          >
            {deleteMode ? 'Exit Delete' : 'Delete'}
          </button>
          <button
            className={viewMode === 'trash' ? 'danger-button' : 'secondary-button'}
            onClick={() => {
              setDeleteConfirmKey('');
              if (viewMode === 'trash') {
                loadFiles('', false, true, true);
              } else {
                loadTrash();
              }
            }}
            type="button"
          >
            {viewMode === 'trash' ? 'Exit Trash' : 'View Trash'}
          </button>
          <button
            className="secondary-button"
            onClick={() => (viewMode === 'trash' ? loadTrash(true) : loadFiles(currentFolderId, true))}
            disabled={refreshing || loading}
            type="button"
          >
            {refreshing ? 'Refreshing...' : 'Refresh'}
          </button>
          <button className="ghost-button" onClick={handleLogout} type="button">Log out</button>
        </div>
      </section>

      <section className="workspace-grid">
        <div className="main-column">
          <section className="panel panel-main">
            <div className="section-head">
              <h2>{viewMode === 'trash' ? 'Trash' : 'Objects'}</h2>
              <span className="count">
                {visibleItems.length === totalItemCount ? `${totalItemCount} items` : `${visibleItems.length} of ${totalItemCount} items`}
              </span>
            </div>

            {viewMode === 'trash' ? (
              <div className="helper-text">Trashed files stay here until you restore them or delete them forever.</div>
            ) : (
              <div className="breadcrumbs" aria-label="Folder path">
                {breadcrumbItems.map((item) => (
                  <button
                    key={item.folder_id || 'root'}
                    className={item.folder_id === currentFolderId ? 'breadcrumb-current' : 'breadcrumb-link'}
                    disabled={item.folder_id === currentFolderId}
                    onClick={() => handleOpenFolder(item.folder_id)}
                    type="button"
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            )}

            <div className="list-controls">
              <label className="field control-field">
                <span>Search</span>
                <input type="text" value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="Search this folder" />
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
                  {recentDeletes.map((item) => <div className="delete-tag" key={item}>{item}</div>)}
                </div>
              </div>
            ) : null}
            {viewMode === 'trash' ? (
              <div className="delete-history-box">
                <div className="delete-history-title">Selection</div>
                <div className="delete-history-list">
                  <button className="secondary-button" onClick={toggleSelectAllTrash} disabled={visibleItems.length === 0 || !!trashAction} type="button">
                    {selectedTrashCount === visibleItems.length && visibleItems.length > 0 ? 'Clear visible selection' : 'Select visible'}
                  </button>
                  <button className="secondary-button" onClick={handleRestoreTrash} disabled={selectedTrashCount === 0 || !!trashAction} type="button">
                    {trashAction === 'restore' ? 'Restoring...' : `Restore selected (${selectedTrashCount})`}
                  </button>
                  <button className="danger-button" onClick={handleDeleteTrash} disabled={selectedTrashCount === 0 || !!trashAction} type="button">
                    {trashAction === 'delete' ? 'Deleting...' : `Delete selected (${selectedTrashCount})`}
                  </button>
                </div>
              </div>
            ) : null}

            {loading ? (
              <div className="empty-state">{viewMode === 'trash' ? 'Loading trash...' : 'Loading files...'}</div>
            ) : totalItemCount === 0 ? (
              <div className="empty-state">{viewMode === 'trash' ? 'Trash is empty.' : 'No files found in this folder.'}</div>
            ) : visibleItems.length === 0 ? (
              <div className="empty-state">{viewMode === 'trash' ? 'No matching files in trash.' : 'No matching items in this folder.'}</div>
            ) : (
              <ul className="file-list">
                {viewMode === 'files' && currentFolderId ? (
                  <li className="file-row folder-row folder-up-row">
                    <button className="folder-open-button" onClick={handleOpenParent} type="button">
                      <span className="folder-icon" aria-hidden="true">DIR</span>
                      <span className="folder-label">..</span>
                    </button>
                  </li>
                ) : null}

                {visibleItems.map((item) => {
                  if (viewMode === 'trash') {
                    const isSelected = !!selectedTrashIds[item.file_id];
                    return (
                      <li className="file-row" key={item.file_id}>
                        <div className="file-meta">
                          <div className="file-name-row">
                            <label className="folder-open-button">
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => toggleTrashSelection(item.file_id)}
                              />
                              <span className="file-name">{item.name || 'Unnamed file'}</span>
                            </label>
                          </div>
                          <div className="file-path">Restore to: {displayPath(item.parent_path || '/')}</div>
                          <dl className="file-details">
                            <div className="file-detail">
                              <dt>Deleted</dt>
                              <dd>{formatDateTime(item.deleted_at)}</dd>
                            </div>
                            <div className="file-detail">
                              <dt>Uploaded</dt>
                              <dd>{formatDateTime(item.upload_date)}</dd>
                            </div>
                          </dl>
                        </div>
                        <div className="row-actions">
                          <button className="secondary-button" onClick={() => {
                            setSelectedTrashIds({ [item.file_id]: true });
                            setDeleteConfirmKey('');
                          }} type="button">
                            Select
                          </button>
                        </div>
                      </li>
                    );
                  }

                  if (item.kind === 'folder') {
                    return (
                      <li className="file-row folder-row" key={item.file_id}>
                        <div className="folder-row-shell">
                          <button className="folder-open-button" onClick={() => handleOpenFolder(item.file_id)} type="button">
                            <span className="folder-icon" aria-hidden="true">DIR</span>
                            <span className="folder-label">{item.name}</span>
                            <span className="folder-path">{item.path}</span>
                          </button>
                          {deleteMode ? (
                            <div className="row-actions">
                              {deleteConfirmKey === item.file_id ? (
                                <>
                                  <button className="danger-button" onClick={() => handleDelete(item.file_id)} disabled={deleting === item.file_id} type="button">
                                    {deleting === item.file_id ? 'Deleting...' : 'Confirm'}
                                  </button>
                                  <button className="ghost-button" onClick={() => setDeleteConfirmKey('')} type="button">Cancel</button>
                                </>
                              ) : (
                                <button className="danger-button" onClick={() => setDeleteConfirmKey(item.file_id)} type="button">Delete</button>
                              )}
                            </div>
                          ) : null}
                        </div>
                      </li>
                    );
                  }

                  const renamePreview = buildRenamedObjectName(renameDrafts[item.file_id] || '', item.file_extension || '');
                  return (
                    <li
                      className="file-row"
                      key={item.file_id}
                      data-file-key={item.file_id}
                      onContextMenu={(event) => {
                        event.preventDefault();
                        setActionMenuKey((current) => (current === item.file_id ? '' : item.file_id));
                      }}
                    >
                      <div className="file-meta">
                        <div className="file-name-row">
                          <div className="file-name">{item.name || 'Unnamed file'}</div>
                          {recentRenames[item.file_id] ? (
                            <div className="rename-tag">
                              {recentRenames[item.file_id].oldName} {'>>'} {recentRenames[item.file_id].newName}
                            </div>
                          ) : null}
                        </div>
                        <div className="file-path">{item.path}</div>
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
                        {actionMenuKey === item.file_id ? (
                          <div className="action-menu">
                            <button
                              className="secondary-button"
                              onClick={() => {
                                setEditingKey(item.file_id);
                                setActionMenuKey('');
                              }}
                              type="button"
                            >
                              Rename
                            </button>
                          </div>
                        ) : null}
                        {editingKey === item.file_id ? (
                          <div className="rename-box">
                            <label className="field grow">
                              <span>Rename file</span>
                              <input
                                type="text"
                                value={renameDrafts[item.file_id] || ''}
                                onChange={(event) => setRenameDrafts((current) => ({ ...current, [item.file_id]: event.target.value }))}
                                placeholder="new-file-name"
                              />
                            </label>
                            <div className="rename-preview">
                              Final name: {renamePreview || 'Enter a new name'}
                            </div>
                            {renameNotices[item.file_id] ? (
                              <div className={renameNotices[item.file_id].type === 'success' ? 'inline-success-box' : 'inline-error-box'}>
                                {renameNotices[item.file_id].message}
                              </div>
                            ) : null}
                            <div className="rename-actions">
                              <button className="secondary-button" onClick={() => handleRename(item)} disabled={!renamePreview || renaming === item.file_id} type="button">
                                {renaming === item.file_id ? 'Renaming...' : 'Rename'}
                              </button>
                              <button
                                className="ghost-button"
                                onClick={() => {
                                  setEditingKey('');
                                  setRenameNotices((current) => ({ ...current, [item.file_id]: null }));
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
                          deleteConfirmKey === item.file_id ? (
                            <>
                              <button className="danger-button" onClick={() => handleDelete(item.file_id)} disabled={deleting === item.file_id} type="button">
                                {deleting === item.file_id ? 'Deleting...' : 'Confirm'}
                              </button>
                              <button className="ghost-button" onClick={() => setDeleteConfirmKey('')} type="button">Cancel</button>
                            </>
                          ) : (
                            <button className="danger-button" onClick={() => setDeleteConfirmKey(item.file_id)} type="button">Delete</button>
                          )
                        ) : null}
                        <button className="ghost-button" onClick={() => setActionMenuKey((current) => (current === item.file_id ? '' : item.file_id))} type="button">
                          Actions
                        </button>
                        <button className="secondary-button" onClick={() => handleDownload(item.file_id)} disabled={downloading === item.file_id} type="button">
                          {downloading === item.file_id ? 'Preparing...' : 'Download'}
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
          {viewMode === 'files' ? (
            <>
              <section className="panel side-panel">
                <div className="section-head">
                  <h2>Create folder</h2>
                </div>
                <form className="stack-form" onSubmit={handleCreateFolder}>
                  <label className="field">
                    <span>Folder name</span>
                    <input type="text" value={folderName} onChange={(event) => setFolderName(event.target.value)} placeholder="new-folder" />
                  </label>
                  <div className="helper-text">Final folder: {buildFolderPreview(currentPath, folderName) || '/'}</div>
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
                    <input type="file" onChange={(event) => setSelectedFile(event.target.files?.[0] || null)} />
                  </label>
                  <div className="helper-text">Current folder: {currentPath || '/'}</div>
                  <button className="primary-button" type="submit" disabled={!selectedFile || uploading}>
                    {uploading ? 'Uploading...' : 'Upload'}
                  </button>
                </form>
              </section>

              <section className="panel side-panel">
                <div className="section-head">
                  <h2>Delete by id</h2>
                </div>
                <div className="stack-form">
                  <label className="field">
                    <span>File id</span>
                    <input type="text" value={deleteObjectId} onChange={(event) => setDeleteObjectId(event.target.value)} placeholder="uuid" />
                  </label>
                  <button className="danger-button" onClick={() => handleDelete(deleteObjectId)} disabled={!normalizeId(deleteObjectId) || deleting} type="button">
                    {deleting && normalizeId(deleteObjectId) === deleting ? 'Deleting...' : 'Delete'}
                  </button>
                </div>
              </section>
            </>
          ) : (
            <section className="panel side-panel">
              <div className="section-head">
                <h2>Trash actions</h2>
              </div>
              <div className="stack-form">
                <div className="helper-text">Selected files restore to their old parent folder. If that folder no longer exists, they go into `/restored/`.</div>
                <button className="secondary-button" onClick={handleRestoreTrash} disabled={selectedTrashCount === 0 || !!trashAction} type="button">
                  {trashAction === 'restore' ? 'Restoring...' : `Restore selected (${selectedTrashCount})`}
                </button>
                <button className="danger-button" onClick={handleDeleteTrash} disabled={selectedTrashCount === 0 || !!trashAction} type="button">
                  {trashAction === 'delete' ? 'Deleting...' : `Delete forever (${selectedTrashCount})`}
                </button>
              </div>
            </section>
          )}

          <section className="panel side-panel panel-muted">
            <div className="section-head">
              <h2>Session</h2>
            </div>
            <p className="subtle panel-note">Your login stays active after reload until you log out or the token expires.</p>
          </section>
        </aside>
      </section>
    </main>
  );
}
