import { useEffect, useMemo, useRef, useState } from 'react';
import Prism from 'prismjs';
import 'prismjs/components/prism-clike';
import 'prismjs/components/prism-markup';
import 'prismjs/components/prism-css';
import 'prismjs/components/prism-javascript';
import 'prismjs/components/prism-jsx';
import 'prismjs/components/prism-typescript';
import 'prismjs/components/prism-tsx';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-markdown';
import 'prismjs/components/prism-yaml';
import 'prismjs/components/prism-xml-doc';
import LoginScreen from './components/LoginScreen';
import PreviewModal from './components/PreviewModal';
import WorkspaceShell from './components/WorkspaceShell';

const API_BASE = '/api';
const AUTH_TOKEN_KEY = 'pcs_auth_token';
const MAX_UPLOAD_BYTES = 1024 * 1024 * 1024;
const MAX_FOLDER_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024;
const MAX_PREVIEW_BYTES = 20 * 1024 * 1024;
const PREVIEWABLE_IMAGE_EXTENSIONS = new Set(['.jpg', '.jpeg', '.png', '.webp', '.gif']);
const PREVIEWABLE_DOCUMENT_EXTENSIONS = new Set(['.pdf']);
const PREVIEWABLE_TEXT_EXTENSIONS = new Set(['.txt', '.md', '.json', '.py', '.js', '.ts', '.jsx', '.tsx', '.html', '.css', '.yml', '.yaml', '.xml', '.log']);
const TEXT_PREVIEW_LANGUAGE_BY_EXTENSION = {
  '.txt': 'plain',
  '.md': 'markdown',
  '.json': 'json',
  '.py': 'python',
  '.js': 'javascript',
  '.ts': 'typescript',
  '.jsx': 'jsx',
  '.tsx': 'tsx',
  '.html': 'markup',
  '.css': 'css',
  '.yml': 'yaml',
  '.yaml': 'yaml',
  '.xml': 'xml-doc',
  '.log': 'plain',
};

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

function ensureFolderPath(path) {
  const normalizedPath = displayPath(path || '/');
  return normalizedPath.endsWith('/') ? normalizedPath : `${normalizedPath}/`;
}

function buildParentPathFromItem(item) {
  const itemPath = String(item?.path || '').trim();
  if (!itemPath) {
    return '/';
  }

  if (item?.kind === 'folder') {
    const normalizedPath = ensureFolderPath(itemPath);
    const trimmed = normalizedPath.replace(/\/$/, '');
    const lastSlashIndex = trimmed.lastIndexOf('/');
    if (lastSlashIndex <= 0) {
      return '/';
    }
    return `${trimmed.slice(0, lastSlashIndex)}/`;
  }

  const lastSlashIndex = itemPath.lastIndexOf('/');
  if (lastSlashIndex <= 0) {
    return '/';
  }
  return `${itemPath.slice(0, lastSlashIndex + 1)}`;
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

function normalizeExtension(value) {
  const normalizedValue = String(value || '').trim().toLowerCase();
  if (!normalizedValue) {
    return '';
  }
  return normalizedValue.startsWith('.') ? normalizedValue : `.${normalizedValue}`;
}

function getPreviewAvailability(item) {
  const extension = normalizeExtension(item?.file_extension);
  const isPreviewableImage = PREVIEWABLE_IMAGE_EXTENSIONS.has(extension);
  const isPreviewableDocument = PREVIEWABLE_DOCUMENT_EXTENSIONS.has(extension);
  const isPreviewableText = PREVIEWABLE_TEXT_EXTENSIONS.has(extension);

  if (!isPreviewableImage && !isPreviewableDocument && !isPreviewableText) {
    return {
      canPreview: false,
      tag: 'No preview',
      reason: 'Preview is only available for jpg, png, webp, gif, pdf, and supported text/code files.',
    };
  }

  const size = Number(item?.size);
  if (!Number.isFinite(size) || size <= 0 || size > MAX_PREVIEW_BYTES) {
    return {
      canPreview: false,
      tag: 'Too large',
      reason: 'Preview is only available for supported files up to 20 MB.',
    };
  }

  return {
    canPreview: true,
    tag: '',
    reason: '',
  };
}

function isPdfPreview(item) {
  return PREVIEWABLE_DOCUMENT_EXTENSIONS.has(normalizeExtension(item?.file_extension));
}

function isTextPreview(item) {
  return PREVIEWABLE_TEXT_EXTENSIONS.has(normalizeExtension(item?.file_extension));
}

function getPreviewLanguage(item) {
  return TEXT_PREVIEW_LANGUAGE_BY_EXTENSION[normalizeExtension(item?.file_extension)] || 'plain';
}

function buildHighlightedPreviewLines(content, language) {
  const normalizedContent = String(content || '').replace(/\r\n/g, '\n');
  if (!normalizedContent) {
    return [''];
  }

  if (language === 'plain') {
    return normalizedContent.split('\n');
  }

  const grammar = Prism.languages[language];
  if (!grammar) {
    return normalizedContent.split('\n');
  }

  return Prism.highlight(normalizedContent, grammar, language).split('\n');
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

function buildFolderUploadSelection(fileList) {
  const files = Array.from(fileList || [])
    .map((entry) => {
      if (entry?.file) {
        return {
          file: entry.file,
          relativePath: String(entry.relativePath || entry.file.webkitRelativePath || entry.file.name || '').replace(/^\/+/, ''),
        };
      }

      const file = entry;
      return {
        file,
        relativePath: String(file?.webkitRelativePath || file?.name || '').replace(/^\/+/, ''),
      };
    })
    .filter((entry) => entry.relativePath);

  if (files.length === 0) {
    return null;
  }

  const firstSegments = files[0].relativePath.split('/');
  const rootName = firstSegments[0] || '';
  const folderPaths = new Set([rootName]);
  let totalBytes = 0;

  const normalizedFiles = files.map((entry) => {
    const segments = entry.relativePath.split('/').filter(Boolean);
    const relativeDir = segments.slice(0, -1).join('/');
    totalBytes += Number(entry.file.size || 0);
    for (let index = 1; index < segments.length - 1; index += 1) {
      folderPaths.add(segments.slice(0, index + 1).join('/'));
    }
    return {
      ...entry,
      relativeDir,
      name: segments[segments.length - 1] || entry.file.name,
      size: Number(entry.file.size || 0),
      type: entry.file.type || '',
    };
  });

  return {
    rootName,
    totalFiles: normalizedFiles.length,
    totalFolders: folderPaths.size,
    totalBytes,
    files: normalizedFiles.sort((left, right) => left.relativePath.localeCompare(right.relativePath, undefined, { sensitivity: 'base' })),
    folderPaths: Array.from(folderPaths).sort((left, right) => {
      const depthDiff = left.split('/').length - right.split('/').length;
      if (depthDiff !== 0) return depthDiff;
      return left.localeCompare(right, undefined, { sensitivity: 'base' });
    }),
  };
}

function readDroppedDirectoryEntries(reader) {
  return new Promise((resolve, reject) => {
    const collectedEntries = [];

    function readNextBatch() {
      reader.readEntries(
        (entries) => {
          if (!entries.length) {
            resolve(collectedEntries);
            return;
          }

          collectedEntries.push(...entries);
          readNextBatch();
        },
        reject,
      );
    }

    readNextBatch();
  });
}

function getDroppedFile(entry) {
  return new Promise((resolve, reject) => {
    entry.file(resolve, reject);
  });
}

async function collectDroppedEntryFiles(entry, basePath = '') {
  if (!entry) {
    return [];
  }

  if (entry.isFile) {
    const file = await getDroppedFile(entry);
    return [{
      file,
      relativePath: `${basePath}${entry.name}`,
    }];
  }

  if (!entry.isDirectory) {
    return [];
  }

  const nextBasePath = `${basePath}${entry.name}/`;
  const children = await readDroppedDirectoryEntries(entry.createReader());
  const collected = [];

  for (const childEntry of children) {
    collected.push(...await collectDroppedEntryFiles(childEntry, nextBasePath));
  }

  return collected;
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

function uploadToPresignedPost(uploadUrl, uploadFields, file, options = {}) {
  const { onProgress } = options;

  return new Promise((resolve, reject) => {
    const formData = new FormData();
    Object.entries(uploadFields || {}).forEach(([key, value]) => {
      formData.append(key, value);
    });
    formData.append('file', file);

    const request = new XMLHttpRequest();
    request.open('POST', uploadUrl);

    request.upload.onprogress = (event) => {
      if (!event.lengthComputable) {
        return;
      }
      onProgress?.(event.loaded, event.total);
    };

    request.onload = () => {
      if (request.status >= 200 && request.status < 300) {
        onProgress?.(file.size, file.size);
        resolve();
        return;
      }

      const message = extractS3UploadError(request.responseText) || `S3 upload failed (${request.status})`;
      reject(new Error(message || 'S3 upload failed'));
    };

    request.onerror = () => {
      reject(new Error('Upload failed before S3 responded'));
    };

    request.onabort = () => {
      reject(new Error('Upload was cancelled'));
    };

    request.send(formData);
  });
}

function extractS3UploadError(payload) {
  const text = String(payload || '').trim();
  if (!text) {
    return '';
  }

  const codeMatch = text.match(/<Code>([^<]+)<\/Code>/i);
  const messageMatch = text.match(/<Message>([^<]+)<\/Message>/i);

  if (codeMatch || messageMatch) {
    const code = codeMatch?.[1]?.trim() || 'S3Error';
    const message = messageMatch?.[1]?.trim() || 'Upload rejected by S3';
    return `${code}: ${message}`;
  }

  return text;
}

export default function App() {
  const folderUploadRuntimeRef = useRef({
    active: false,
    logId: '',
    authToken: '',
    phase: '',
    currentPath: '',
  });
  const dragDropDepthRef = useRef(0);
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
  const [currentFolderState, setCurrentFolderState] = useState(null);
  const [breadcrumbItems, setBreadcrumbItems] = useState([{ label: 'Root', folder_id: '' }]);
  const [viewMode, setViewMode] = useState('files');
  const [contentView, setContentView] = useState('objects');
  const [treeScope, setTreeScope] = useState('root');
  const [treeVisibility, setTreeVisibility] = useState('active');
  const [treeData, setTreeData] = useState(null);
  const [treeLoading, setTreeLoading] = useState(false);
  const [treeError, setTreeError] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [sortMode, setSortMode] = useState('alpha-asc');
  const [groupMode, setGroupMode] = useState('folder-first');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [selectedFile, setSelectedFile] = useState(null);
  const [selectedFolderUpload, setSelectedFolderUpload] = useState(null);
  const [dragDropActive, setDragDropActive] = useState(false);
  const [replaceExistingUpload, setReplaceExistingUpload] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [folderUploading, setFolderUploading] = useState(false);
  const [uploadEntries, setUploadEntries] = useState([]);
  const [previewItem, setPreviewItem] = useState(null);
  const [previewUrl, setPreviewUrl] = useState('');
  const [previewTextContent, setPreviewTextContent] = useState('');
  const [previewLoadingId, setPreviewLoadingId] = useState('');
  const [folderName, setFolderName] = useState('');
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [deleteObjectId, setDeleteObjectId] = useState('');
  const [deleteMode, setDeleteMode] = useState(false);
  const [purgeMode, setPurgeMode] = useState(false);
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
  const [selectedDeleteIds, setSelectedDeleteIds] = useState({});
  const [purgeState, setPurgeState] = useState({
    purge_active: false,
    root_id: '',
    root_parent_folder_id: '',
    phase: 'idle',
  });
  const [purgeFolderId, setPurgeFolderId] = useState('');
  const [purging, setPurging] = useState(false);
  const [purgeFolderLabel, setPurgeFolderLabel] = useState('');
  const [moveSelectionMode, setMoveSelectionMode] = useState(false);
  const [moveState, setMoveState] = useState({
    move_active: false,
    log_id: '',
    operation_id: '',
    source_id: '',
    destination_folder_id: '',
    phase: 'idle',
    mode: '',
    source_kind: '',
  });
  const [moveSourceId, setMoveSourceId] = useState('');
  const [moveSourceLabel, setMoveSourceLabel] = useState('');
  const [moveSourceKind, setMoveSourceKind] = useState('');
  const [moveSourceParentPath, setMoveSourceParentPath] = useState('/');
  const [moveDestinationId, setMoveDestinationId] = useState('');
  const [moveDestinationLabel, setMoveDestinationLabel] = useState('');
  const [moveRunning, setMoveRunning] = useState(false);

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
  const visibleFileItems = useMemo(() => visibleItems.filter((item) => item.kind === 'file'), [visibleItems]);
  const selectedDeleteCount = Object.values(selectedDeleteIds).filter(Boolean).length;
  const previewLanguage = useMemo(() => getPreviewLanguage(previewItem), [previewItem]);
  const previewLines = useMemo(
    () => buildHighlightedPreviewLines(previewTextContent, previewLanguage),
    [previewLanguage, previewTextContent],
  );

  function addUploadEntry(entry) {
    setUploadEntries((current) => [entry, ...current].slice(0, 6));
  }

  function setOngoingFolderUpload(nextValues) {
    folderUploadRuntimeRef.current = {
      ...folderUploadRuntimeRef.current,
      ...nextValues,
      active: true,
    };
  }

  function clearOngoingFolderUpload() {
    folderUploadRuntimeRef.current = {
      active: false,
      logId: '',
      authToken: '',
      phase: '',
      currentPath: '',
    };
  }

  function updateUploadEntry(entryId, nextValues) {
    setUploadEntries((current) => current.map((entry) => (
      entry.id === entryId ? { ...entry, ...nextValues } : entry
    )));
  }

  function clearWorkspaceState() {
    setFolders([]);
    setFiles([]);
    setTrashedFiles([]);
    setCurrentFolderId('');
    setCurrentPath('/');
    setCurrentFolderState(null);
    setBreadcrumbItems([{ label: 'Root', folder_id: '' }]);
    setViewMode('files');
    setContentView('objects');
    setTreeScope('root');
    setTreeVisibility('active');
    setTreeData(null);
    setTreeLoading(false);
    setTreeError('');
    setSearchQuery('');
    setSortMode('alpha-asc');
    setGroupMode('folder-first');
    setLoading(true);
    setRefreshing(false);
    setError('');
    setSuccess('');
    setSelectedFile(null);
    setSelectedFolderUpload(null);
    setDragDropActive(false);
    dragDropDepthRef.current = 0;
    setReplaceExistingUpload(false);
    setUploading(false);
    setFolderUploading(false);
    setUploadEntries([]);
    setPreviewItem(null);
    setPreviewUrl('');
    setPreviewTextContent('');
    setPreviewLoadingId('');
    setFolderName('');
    setCreatingFolder(false);
    setDeleteObjectId('');
    setDeleteMode(false);
    setPurgeMode(false);
    setMoveSelectionMode(false);
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
    setSelectedDeleteIds({});
    setPurgeState({
      purge_active: false,
      root_id: '',
      root_parent_folder_id: '',
      phase: 'idle',
    });
    setPurgeFolderId('');
    setPurging(false);
    setPurgeFolderLabel('');
    setMoveState({
      move_active: false,
      log_id: '',
      operation_id: '',
      source_id: '',
      destination_folder_id: '',
      phase: 'idle',
      mode: '',
      source_kind: '',
    });
    setMoveSourceId('');
    setMoveSourceLabel('');
    setMoveSourceKind('');
    setMoveSourceParentPath('/');
    setMoveDestinationId('');
    setMoveDestinationLabel('');
    setMoveRunning(false);
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
      if (isManualRefresh) {
        if (!preserveRecentRenames) {
          setRecentRenames({});
        }
        if (!preserveRecentDeletes) {
          setRecentDeletes([]);
        }
        setUploadEntries([]);
        setRefreshing(true);
      } else {
        setLoading(true);
      }

      const suffix = normalizedFolderId ? `?folder_id=${encodeURIComponent(normalizedFolderId)}` : '';
      const data = await api(`/files${suffix}`, { authToken });
      setCurrentFolderId(data?.current_folder_id || '');
      setCurrentPath(displayPath(data?.current_path || '/'));
      setCurrentFolderState(data?.current_folder_state || null);
      setBreadcrumbItems(Array.isArray(data?.breadcrumbs) && data.breadcrumbs.length > 0 ? data.breadcrumbs : [{ label: 'Root', folder_id: '' }]);
      setFolders(Array.isArray(data?.folders) ? data.folders : []);
      setFiles(Array.isArray(data?.files) ? data.files : []);
      const partialErrors = data?.partial_errors && typeof data.partial_errors === 'object' ? data.partial_errors : {};
      const partialMessages = Object.values(partialErrors).filter(Boolean);
      if (partialMessages.length > 0) {
        setError(partialMessages.join(' '));
      }
      setTrashedFiles([]);
      setSelectedTrashIds({});
      setSelectedDeleteIds({});
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
        setUploadEntries([]);
        setRefreshing(true);
      } else {
        setLoading(true);
      }

      const data = await api('/trash', { authToken });
      setFolders([]);
      setFiles([]);
      setCurrentFolderId('');
      setCurrentPath('/trash');
      setCurrentFolderState(null);
      setBreadcrumbItems([{ label: 'Trash', folder_id: '' }]);
      setTrashedFiles(Array.isArray(data?.files) ? data.files : []);
      setSelectedTrashIds({});
      setSelectedDeleteIds({});
      setDeleteMode(false);
      setDeleteConfirmKey('');
      setViewMode('trash');
      setContentView('objects');
      setTreeData(null);
      setTreeError('');
      setTreeLoading(false);
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

  async function loadTreeData({
    folderId = currentFolderId,
    scope = treeScope,
    visibility = treeVisibility,
    targetViewMode = viewMode,
  } = {}) {
    if (targetViewMode === 'trash') {
      setTreeData(null);
      setTreeError('');
      setTreeLoading(false);
      return;
    }

    try {
      setTreeError('');
      setTreeLoading(true);
      const params = new URLSearchParams();
      if (scope === 'current' && normalizeId(folderId)) {
        params.set('root_folder_id', normalizeId(folderId));
      }
      if (visibility === 'all') {
        params.set('include_deleted', 'true');
      }
      const suffix = params.toString() ? `?${params.toString()}` : '';
      const data = await api(`/tree${suffix}`, { authToken });
      setTreeData(data?.tree || null);
    } catch (err) {
      if (err.message === 'Invalid or expired token') {
        handleLogout();
        setAuthError('Your session expired. Please log in again.');
      } else {
        setTreeError(err.message || 'Could not load tree');
      }
    } finally {
      setTreeLoading(false);
    }
  }

  async function loadPurgeState() {
    try {
      const data = await api('/purge/state', { authToken });
      setPurgeState({
        purge_active: !!data?.purge_active,
        root_id: data?.root_id || '',
        root_parent_folder_id: data?.root_parent_folder_id || '',
        phase: data?.phase || 'idle',
      });
    } catch (err) {
      if (err.message === 'Invalid or expired token') {
        handleLogout();
        setAuthError('Your session expired. Please log in again.');
      } else {
        setError(err.message || 'Could not load purge state');
      }
    }
  }

  async function loadMoveState() {
    try {
      const data = await api('/move/state', { authToken });
      setMoveState({
        move_active: !!data?.move_active,
        log_id: data?.log_id || '',
        operation_id: data?.operation_id || '',
        source_id: data?.source_id || '',
        destination_folder_id: data?.destination_folder_id || '',
        phase: data?.phase || 'idle',
        mode: data?.mode || '',
        source_kind: data?.source_kind || '',
      });
    } catch (err) {
      if (err.message === 'Invalid or expired token') {
        handleLogout();
        setAuthError('Your session expired. Please log in again.');
      } else {
        setError(err.message || 'Could not load move state');
      }
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
      loadPurgeState();
      loadMoveState();
    }
  }, [authUser, authToken]);

  useEffect(() => {
    if (!authUser || !authToken) {
      return;
    }
    if (viewMode !== 'files' || contentView !== 'tree') {
      return;
    }
    loadTreeData({
      folderId: currentFolderId,
      scope: treeScope,
      visibility: treeVisibility,
      targetViewMode: viewMode,
    });
  }, [authUser, authToken, contentView, currentFolderId, treeScope, treeVisibility, viewMode]);

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

  useEffect(() => {
    function handlePageHide() {
      const session = folderUploadRuntimeRef.current;
      if (!session.active || !session.logId || !session.authToken) {
        return;
      }
      if (session.phase === 'transferred' || session.phase === 'finalizing' || session.phase === 'done') {
        return;
      }

      fetch(`${API_BASE}/folder-upload/fail`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${session.authToken}`,
        },
        body: JSON.stringify({
          log_id: session.logId,
          last_error: 'Folder upload interrupted by page reload or navigation',
          current_path: session.currentPath || '',
        }),
        keepalive: true,
      }).catch(() => {
        // Best effort rollback when the page is leaving.
      });

      clearOngoingFolderUpload();
    }

    window.addEventListener('pagehide', handlePageHide);
    return () => window.removeEventListener('pagehide', handlePageHide);
  }, []);

  useEffect(() => {
    if (!moveSelectionMode || !moveSourceId || !moveDestinationId) {
      return;
    }

    const selectedDestination = folders.find((item) => normalizeId(item.file_id || item.folder_id) === normalizeId(moveDestinationId));
    if (!selectedDestination) {
      return;
    }

    if (!getMoveDestinationBlockReason(selectedDestination)) {
      return;
    }

    setMoveDestinationId('');
    setMoveDestinationLabel('');
  }, [
    currentPath,
    moveDestinationId,
    moveSelectionMode,
    moveSourceId,
    moveSourceKind,
    moveSourceLabel,
    moveSourceParentPath,
    folders,
  ]);

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

  async function runSingleFileUpload(file, { replaceExisting = replaceExistingUpload } = {}) {
    if (!file) return false;
    const entryId = `upload-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

    if (file.size > MAX_UPLOAD_BYTES) {
      setError('File is larger than 1GB.');
      return false;
    }

    try {
      setError('');
      setSuccess('');
      setUploading(true);
      addUploadEntry({
        id: entryId,
        name: file.name,
        size: file.size,
        progress: 0,
        status: 'preparing',
        targetPath: currentPath || '/',
        mode: replaceExisting ? 'replace' : 'upload',
        errorMessage: '',
      });
      const uploadInit = await api('/upload/init', {
        method: 'POST',
        body: JSON.stringify({
          file_name: file.name,
          file_size: file.size,
          file_type: file.type || '',
          folder_id: currentFolderId,
          replace_existing: replaceExisting,
        }),
        authToken,
      });
      updateUploadEntry(entryId, {
        status: 'uploading',
      });

      await uploadToPresignedPost(
        uploadInit?.upload_url || '',
        uploadInit?.upload_fields || {},
        file,
        {
          onProgress: (loaded, total) => {
            const safeTotal = total > 0 ? total : file.size;
            const progress = safeTotal > 0 ? Math.min(Math.round((loaded / safeTotal) * 100), 100) : 0;
            updateUploadEntry(entryId, {
              progress,
              status: 'uploading',
            });
          },
        },
      );

      await api('/upload/complete', {
        method: 'POST',
        body: JSON.stringify({
          upload_token: uploadInit?.upload_token || '',
        }),
        authToken,
      });
      updateUploadEntry(entryId, {
        progress: 100,
        status: 'success',
        errorMessage: '',
      });
      await loadFiles(currentFolderId, true);
      return true;
    } catch (err) {
      updateUploadEntry(entryId, {
        status: 'failed',
        errorMessage: err.message || 'Upload failed',
      });
      setError(err.message || 'Upload failed');
      return false;
    } finally {
      setUploading(false);
    }
  }

  async function handleUpload(event) {
    event.preventDefault();
    if (!selectedFile) return;
    const form = event.currentTarget;
    const succeeded = await runSingleFileUpload(selectedFile, { replaceExisting: replaceExistingUpload });
    if (!succeeded) {
      return;
    }

    setSelectedFile(null);
    setReplaceExistingUpload(false);
    form.reset();
  }

  function handleSelectedFileChange(event) {
    const file = event.target.files?.[0] || null;
    if (!file) {
      setSelectedFile(null);
      return;
    }

    if (file.size > MAX_UPLOAD_BYTES) {
      setSelectedFile(null);
      setError('File is larger than 1GB.');
      event.target.value = '';
      return;
    }

    setError('');
    setSelectedFile(file);
  }

  function handleSelectedFolderChange(event) {
    const summary = buildFolderUploadSelection(event.target.files);
    if (!summary) {
      setSelectedFolderUpload(null);
      return;
    }

    if (summary.totalBytes > MAX_FOLDER_UPLOAD_BYTES) {
      setSelectedFolderUpload(null);
      setError('Selected folder is larger than 2GB.');
      event.target.value = '';
      return;
    }

    setError('');
    setSelectedFolderUpload(summary);
  }

  function handleUploadDragEnter(event) {
    if (viewMode !== 'files' || contentView !== 'objects') {
      return;
    }
    event.preventDefault();
    dragDropDepthRef.current += 1;
    setDragDropActive(true);
  }

  function handleUploadDragOver(event) {
    if (viewMode !== 'files' || contentView !== 'objects') {
      return;
    }
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = 'copy';
    }
    setDragDropActive(true);
  }

  function handleUploadDragLeave(event) {
    if (viewMode !== 'files' || contentView !== 'objects') {
      return;
    }
    event.preventDefault();
    dragDropDepthRef.current = Math.max(dragDropDepthRef.current - 1, 0);
    if (dragDropDepthRef.current === 0) {
      setDragDropActive(false);
    }
  }

  async function handleUploadDrop(event) {
    if (viewMode !== 'files' || contentView !== 'objects') {
      return;
    }
    event.preventDefault();
    dragDropDepthRef.current = 0;
    setDragDropActive(false);

    if (uploading || folderUploading) {
      setError('Wait for the current upload to finish first.');
      return;
    }

    try {
      setError('');
      setSuccess('');
      const transferItems = Array.from(event.dataTransfer?.items || []);
      const transferFiles = Array.from(event.dataTransfer?.files || []);
      const droppedEntries = transferItems
        .map((item) => (typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null))
        .filter(Boolean);
      const droppedDirectories = droppedEntries.filter((entry) => entry.isDirectory);
      const droppedFiles = droppedEntries.filter((entry) => entry.isFile);

      if (droppedDirectories.length > 0) {
        if (droppedDirectories.length > 1 || droppedFiles.length > 0) {
          throw new Error('Drop one folder at a time. Mixed file and folder drops are not supported yet.');
        }

        const droppedFolderFiles = await collectDroppedEntryFiles(droppedDirectories[0]);
        const summary = buildFolderUploadSelection(droppedFolderFiles);
        if (!summary) {
          throw new Error('Could not read the dropped folder.');
        }
        if (summary.totalBytes > MAX_FOLDER_UPLOAD_BYTES) {
          throw new Error('Selected folder is larger than 2GB.');
        }

        setSelectedFolderUpload(summary);
        const succeeded = await runFolderUpload(summary);
        if (succeeded) {
          setSelectedFolderUpload(null);
        }
        return;
      }

      const filesToUpload = transferFiles.filter((file) => Number(file?.size || 0) > 0);
      if (!filesToUpload.length) {
        throw new Error('Drop a file or folder to upload.');
      }

      for (const file of filesToUpload) {
        const succeeded = await runSingleFileUpload(file, { replaceExisting: replaceExistingUpload });
        if (!succeeded) {
          break;
        }
      }
    } catch (err) {
      setError(err.message || 'Drag and drop upload failed');
    }
  }

  async function runFolderUpload(summary) {
    if (!summary) return false;
    const entryId = `folder-upload-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    let uploadSession = null;
    let lastCurrentPath = '';
    let uploadedFiles = 0;
    let uploadedBytes = 0;
    let transferCompleted = false;

    try {
      setError('');
      setSuccess('');
      setFolderUploading(true);
      setOngoingFolderUpload({
        authToken,
        phase: 'starting',
        currentPath: summary.rootName,
      });
      addUploadEntry({
        id: entryId,
        name: `${summary.rootName}/`,
        size: summary.totalBytes,
        progress: 0,
        status: 'preparing',
        targetPath: currentPath || '/',
        mode: 'folder',
        errorMessage: '',
      });

      uploadSession = await api('/folder-upload/start', {
        method: 'POST',
        body: JSON.stringify({
          root_folder_name: summary.rootName,
          parent_id: currentFolderId,
          total_files: summary.totalFiles,
          total_bytes: summary.totalBytes,
        }),
        authToken,
      });
      setOngoingFolderUpload({
        authToken,
        logId: uploadSession.log_id,
        phase: 'creating_folder_tree',
        currentPath: summary.rootName,
      });

      const folderIdByPath = new Map([[summary.rootName, uploadSession.root_folder_id]]);
      updateUploadEntry(entryId, { status: 'uploading' });

      for (const folderPath of summary.folderPaths) {
        if (folderPath === summary.rootName) {
          continue;
        }
        const lastSlashIndex = folderPath.lastIndexOf('/');
        const parentPath = lastSlashIndex > 0 ? folderPath.slice(0, lastSlashIndex) : summary.rootName;
        const folderName = lastSlashIndex >= 0 ? folderPath.slice(lastSlashIndex + 1) : folderPath;
        const parentFolderId = folderIdByPath.get(parentPath) || uploadSession.root_folder_id;
        lastCurrentPath = folderPath;
        setOngoingFolderUpload({
          authToken,
          logId: uploadSession.log_id,
          phase: 'creating_folder_tree',
          currentPath: folderPath,
        });
        await api('/folders', {
          method: 'POST',
          body: JSON.stringify({
            name: folderName,
            parent_id: parentFolderId,
            folder_upload_operation_id: uploadSession.operation_id,
            folder_upload_root_id: uploadSession.root_folder_id,
          }),
          authToken,
        }).then((data) => {
          folderIdByPath.set(folderPath, data?.created_folder_id || '');
        });
        await api('/folder-upload/progress', {
          method: 'POST',
          body: JSON.stringify({
            log_id: uploadSession.log_id,
            phase: 'creating_folder_tree',
            current_path: folderPath,
            uploaded_files: uploadedFiles,
            uploaded_bytes: uploadedBytes,
          }),
          authToken,
        });
      }

      for (const entry of summary.files) {
        const parentPath = entry.relativeDir || summary.rootName;
        const parentFolderId = folderIdByPath.get(parentPath) || uploadSession.root_folder_id;
        lastCurrentPath = entry.relativePath;
        setOngoingFolderUpload({
          authToken,
          logId: uploadSession.log_id,
          phase: 'uploading_files',
          currentPath: entry.relativePath,
        });
        const uploadInit = await api('/upload/init', {
          method: 'POST',
          body: JSON.stringify({
            file_name: entry.name,
            file_size: entry.size,
            file_type: entry.type,
            folder_id: parentFolderId,
            replace_existing: false,
            folder_upload_operation_id: uploadSession.operation_id,
            folder_upload_root_id: uploadSession.root_folder_id,
          }),
          authToken,
        });

        await uploadToPresignedPost(
          uploadInit?.upload_url || '',
          uploadInit?.upload_fields || {},
          entry.file,
          {
            onProgress: (loaded) => {
              const safeLoaded = Math.min(Number(loaded || 0), entry.size);
              const totalLoaded = uploadedBytes + safeLoaded;
              const progress = summary.totalBytes > 0
                ? Math.min(Math.round((totalLoaded / summary.totalBytes) * 100), 100)
                : 0;
              updateUploadEntry(entryId, {
                progress,
                status: 'uploading',
              });
            },
          },
        );

        await api('/upload/complete', {
          method: 'POST',
          body: JSON.stringify({
            upload_token: uploadInit?.upload_token || '',
          }),
          authToken,
        });

        uploadedFiles += 1;
        uploadedBytes += entry.size;
        updateUploadEntry(entryId, {
          progress: summary.totalBytes > 0
            ? Math.min(Math.round((uploadedBytes / summary.totalBytes) * 100), 100)
            : 100,
          status: 'uploading',
        });
        await api('/folder-upload/progress', {
          method: 'POST',
          body: JSON.stringify({
            log_id: uploadSession.log_id,
            phase: 'uploading_files',
            current_path: entry.relativePath,
            last_uploaded_file_path: entry.relativePath,
            uploaded_files: uploadedFiles,
            uploaded_bytes: uploadedBytes,
          }),
          authToken,
        });
      }

      transferCompleted = true;
      setOngoingFolderUpload({
        authToken,
        logId: uploadSession.log_id,
        phase: 'finalizing',
        currentPath: lastCurrentPath || summary.rootName,
      });
      await api('/folder-upload/finalize', {
        method: 'POST',
        body: JSON.stringify({
          log_id: uploadSession.log_id,
        }),
        authToken,
      });

      updateUploadEntry(entryId, {
        progress: 100,
        status: 'success',
        errorMessage: '',
      });
      clearOngoingFolderUpload();
      await loadFiles(currentFolderId, true);
      return true;
    } catch (err) {
      if (uploadSession?.log_id && !transferCompleted) {
        try {
          await api('/folder-upload/fail', {
            method: 'POST',
            body: JSON.stringify({
              log_id: uploadSession.log_id,
              last_error: err.message || 'Folder upload failed',
              current_path: lastCurrentPath,
            }),
            authToken,
          });
        } catch {
          // Preserve the original upload error for the user.
        }
      }

      updateUploadEntry(entryId, {
        status: 'failed',
        errorMessage: err.message || 'Folder upload failed',
      });
      setError(err.message || 'Folder upload failed');
      clearOngoingFolderUpload();
      await loadFiles(currentFolderId, true, true, true);
      return false;
    } finally {
      setFolderUploading(false);
    }
  }

  async function handleFolderUpload(event) {
    event.preventDefault();
    if (!selectedFolderUpload) return;

    const form = event.currentTarget;
    const succeeded = await runFolderUpload(selectedFolderUpload);
    if (!succeeded) {
      return;
    }

    setSelectedFolderUpload(null);
    form.reset();
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

  async function handlePreview(file) {
    const fileId = normalizeId(file?.file_id);
    if (!fileId) return;

    const previewAvailability = getPreviewAvailability(file);
    if (!previewAvailability.canPreview) {
      return;
    }

    try {
      setError('');
      setSuccess('');
      setPreviewLoadingId(fileId);
      const data = await api(`/files/${encodeURIComponent(fileId)}/preview`, { authToken });
      if (!data?.url) {
        throw new Error('Preview URL not found');
      }
      if (isTextPreview(file)) {
        const previewTextData = await api(`/files/${encodeURIComponent(fileId)}/preview-text`, { authToken });
        setPreviewTextContent(String(previewTextData?.content || ''));
      } else {
        setPreviewTextContent('');
      }
      setPreviewItem(file);
      setPreviewUrl(data.url);
    } catch (err) {
      setError(err.message || 'Preview failed');
    } finally {
      setPreviewLoadingId('');
    }
  }

  function closePreview() {
    setPreviewItem(null);
    setPreviewUrl('');
    setPreviewTextContent('');
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

  async function handlePurge({ resume = false } = {}) {
    const folderId = resume ? '' : normalizeId(purgeFolderId);
    if (!resume && !folderId) return;

    try {
      setError('');
      setSuccess('');
      setPurging(true);
      const data = await api('/purge', {
        method: 'POST',
        body: JSON.stringify({ folder_id: folderId }),
        authToken,
      });
      const deletedFiles = Number(data?.deleted_files || 0);
      const deletedFolders = Number(data?.deleted_folders || 0);
      const rootFolderId = data?.root_folder_id || folderId || purgeState.root_id;
      setPurgeFolderId('');
      setPurgeFolderLabel('');
      setSuccess(
        `${data?.resumed ? 'Resumed' : 'Started'} purge for ${rootFolderId}. Removed ${deletedFolders} folder${deletedFolders === 1 ? '' : 's'} and ${deletedFiles} file${deletedFiles === 1 ? '' : 's'}.`,
      );
      await loadFiles('', true, true, true);
      await loadPurgeState();
    } catch (err) {
      setError(err.message || 'Purge failed');
      await loadPurgeState();
    } finally {
      setPurging(false);
    }
  }

  function selectFolderForPurge(folder) {
    const folderId = normalizeId(folder?.file_id || folder?.folder_id);
    if (!folderId) return;

    setPurgeFolderId(folderId);
    setPurgeFolderLabel(folder?.path || folder?.name || folderId);
    setSuccess(`Selected ${folder?.path || folder?.name || folderId} for purge.`);
    setError('');
  }

  function togglePurgeMode() {
    if (viewMode === 'trash') {
      return;
    }

    setDeleteConfirmKey('');
    setDeleteMode(false);
    setMoveSelectionMode(false);
    setPurgeMode((current) => {
      if (current) {
        setPurgeFolderId('');
        setPurgeFolderLabel('');
      }
      return !current;
    });
  }

  function toggleMoveSelectionMode() {
    if (viewMode === 'trash') {
      return;
    }

    setDeleteConfirmKey('');
    setDeleteMode(false);
    setPurgeMode(false);
    setMoveSelectionMode((current) => {
      if (current) {
        setMoveSourceId('');
        setMoveSourceLabel('');
        setMoveSourceKind('');
        setMoveSourceParentPath('/');
        setMoveDestinationId('');
        setMoveDestinationLabel('');
      }
      return !current;
    });
  }

  function selectItemForMoveSource(item) {
    const sourceId = normalizeId(item?.file_id || item?.folder_id);
    if (!sourceId) return;

    setMoveSourceId(sourceId);
    setMoveSourceLabel(item?.path || item?.name || sourceId);
    setMoveSourceKind(item?.kind || '');
    setMoveSourceParentPath(buildParentPathFromItem(item));
    if (normalizeId(moveDestinationId) === sourceId) {
      setMoveDestinationId('');
      setMoveDestinationLabel('');
    }
    setSuccess(`Selected ${item?.path || item?.name || sourceId} as move source.`);
    setError('');
  }

  function selectFolderForMoveDestination(folder) {
    const destinationId = normalizeId(folder?.file_id || folder?.folder_id);
    if (!destinationId) return;

    setMoveDestinationId(destinationId);
    setMoveDestinationLabel(folder?.path || folder?.name || destinationId);
    setSuccess(`Selected ${folder?.path || folder?.name || destinationId} as move destination.`);
    setError('');
  }

  function useCurrentFolderAsMoveDestination() {
    setMoveDestinationId(currentFolderId || '');
    setMoveDestinationLabel(currentPath || '/');
    setSuccess(`Selected ${currentPath || '/'} as move destination.`);
    setError('');
  }

  function getMoveDestinationBlockReason(folder) {
    if (!moveSelectionMode || !moveSourceId) {
      return '';
    }

    const folderId = normalizeId(folder?.file_id || folder?.folder_id);
    if (!folderId) {
      return '';
    }

    if (folderId === normalizeId(moveSourceId)) {
      return 'Not allowed: source';
    }

    if (moveSourceKind === 'folder') {
      const sourcePath = ensureFolderPath(moveSourceLabel || '/');
      const destinationPath = ensureFolderPath(folder?.path || '/');
      if (destinationPath.startsWith(sourcePath)) {
        return 'Not allowed: inside source';
      }
      return '';
    }

    if (moveSourceKind === 'file') {
      const destinationPath = ensureFolderPath(folder?.path || '/');
      if (destinationPath === ensureFolderPath(moveSourceParentPath || '/')) {
        return 'Not allowed: same folder';
      }
    }

    return '';
  }

  function getCurrentFolderMoveBlockReason() {
    if (!moveSelectionMode || !moveSourceId) {
      return '';
    }

    if (moveSourceKind === 'folder') {
      if (normalizeId(currentFolderId) === normalizeId(moveSourceId)) {
        return 'Not allowed: source';
      }
      const sourcePath = ensureFolderPath(moveSourceLabel || '/');
      const destinationPath = ensureFolderPath(currentPath || '/');
      if (destinationPath.startsWith(sourcePath)) {
        return 'Not allowed: inside source';
      }
      return '';
    }

    if (moveSourceKind === 'file' && ensureFolderPath(currentPath || '/') === ensureFolderPath(moveSourceParentPath || '/')) {
      return 'Not allowed: same folder';
    }

    return '';
  }

  async function handleMoveOperation(mode, { resume = false } = {}) {
    const sourceId = resume ? '' : normalizeId(moveSourceId);
    const destinationId = resume ? '' : normalizeId(moveDestinationId);
    if (!resume && !sourceId) return;

    try {
      setError('');
      setSuccess('');
      setMoveRunning(true);
      const data = await api('/move', {
        method: 'POST',
        body: JSON.stringify({
          source_id: sourceId,
          destination_folder_id: destinationId,
          mode,
        }),
        authToken,
      });
      setMoveSourceId('');
      setMoveSourceLabel('');
      setMoveSourceKind('');
      setMoveSourceParentPath('/');
      setMoveDestinationId('');
      setMoveDestinationLabel('');
      setSuccess(
        `${data?.resumed ? 'Resumed' : 'Started'} move for ${data?.source_entry_id || sourceId}. Copied ${Number(data?.moved_folders || 0)} folder${Number(data?.moved_folders || 0) === 1 ? '' : 's'} and ${Number(data?.moved_files || 0)} file${Number(data?.moved_files || 0) === 1 ? '' : 's'}.`,
      );
      await loadFiles(currentFolderId, true, true, true);
      await loadMoveState();
      await loadPurgeState();
    } catch (err) {
      setError(err.message || 'Move failed');
      await loadMoveState();
    } finally {
      setMoveRunning(false);
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

  function toggleDeleteSelection(objectId) {
    setSelectedDeleteIds((current) => ({
      ...current,
      [objectId]: !current[objectId],
    }));
  }

  function toggleSelectAllFilesForDelete() {
    if (visibleFileItems.length === 0) return;
    const shouldSelectAll = visibleFileItems.some((item) => !selectedDeleteIds[item.file_id]);
    const nextSelection = {};

    visibleFileItems.forEach((item) => {
      nextSelection[item.file_id] = shouldSelectAll;
    });

    setSelectedDeleteIds(nextSelection);
  }

  async function handleSoftDeleteSelected() {
    const fileIds = Object.entries(selectedDeleteIds)
      .filter(([, isSelected]) => isSelected)
      .map(([fileId]) => fileId);

    if (fileIds.length === 0) return;

    try {
      setError('');
      setSuccess('');
      setTrashAction('soft-delete');
      const selectedPaths = files
        .filter((item) => fileIds.includes(item.file_id))
        .map((item) => item.path)
        .filter(Boolean);
      const data = await api('/trash/soft-delete', {
        method: 'POST',
        body: JSON.stringify({ file_ids: fileIds }),
        authToken,
      });
      setSelectedDeleteIds({});
      setDeleteConfirmKey('');
      setRecentDeletes((current) => {
        const next = [...selectedPaths, ...current.filter((entry) => !selectedPaths.includes(entry))];
        return next.slice(0, 5);
      });
      const deletedCount = Array.isArray(data?.deleted) ? data.deleted.length : fileIds.length;
      setSuccess(`Moved ${deletedCount} file${deletedCount === 1 ? '' : 's'} to trash.`);
      await loadFiles(currentFolderId, true, true, true);
    } catch (err) {
      setError(err.message || 'Move to trash failed');
    } finally {
      setTrashAction('');
    }
  }

  function toggleDeleteMode() {
    if (viewMode === 'trash') {
      return;
    }
    setDeleteConfirmKey('');
    setSelectedDeleteIds({});
    setPurgeMode(false);
    setMoveSelectionMode(false);
    setPurgeFolderId('');
    setPurgeFolderLabel('');
    setDeleteMode((current) => !current);
  }

  function toggleTrashView() {
    setDeleteConfirmKey('');
    setDeleteMode(false);
    setPurgeMode(false);
    setMoveSelectionMode(false);
    setPurgeFolderId('');
    setPurgeFolderLabel('');
    setContentView('objects');
    if (viewMode === 'trash') {
      loadFiles('', false, true, true);
    } else {
      loadTrash();
    }
  }

  function switchContentView(nextView) {
    if (viewMode === 'trash') {
      return;
    }
    setDeleteConfirmKey('');
    setDeleteMode(false);
    setPurgeMode(false);
    setMoveSelectionMode(false);
    setPurgeFolderId('');
    setPurgeFolderLabel('');
    setContentView(nextView);
  }

  function refreshCurrentView() {
    if (viewMode === 'trash') {
      loadTrash(true);
      return;
    }
    if (contentView === 'tree') {
      loadTreeData({
        folderId: currentFolderId,
        scope: treeScope,
        visibility: treeVisibility,
        targetViewMode: viewMode,
      });
      return;
    }
    loadFiles(currentFolderId, true);
  }

  function selectOnlyTrashItem(fileId) {
    setSelectedTrashIds({ [fileId]: true });
    setDeleteConfirmKey('');
  }

  function togglePurgeFolderSelection(item) {
    const itemId = normalizeId(item?.path || item?.name || item?.file_id);
    if (normalizeId(purgeFolderId) === normalizeId(item.file_id)) {
      setPurgeFolderId('');
      setPurgeFolderLabel('');
      setSuccess(`Cleared purge selection for ${item.path || item.name || item.file_id}.`);
      setError('');
      return;
    }
    selectFolderForPurge(item);
  }

  function toggleMoveSourceSelection(item) {
    const sourceId = normalizeId(item?.file_id || item?.folder_id);
    if (normalizeId(moveSourceId) === sourceId) {
      setMoveSourceId('');
      setMoveSourceLabel('');
      setMoveSourceKind('');
      setSuccess(`Cleared move source for ${item.path || item.name || item.file_id}.`);
      setError('');
      return;
    }
    selectItemForMoveSource(item);
  }

  function toggleMoveDestinationSelection(item) {
    const destinationId = normalizeId(item?.file_id || item?.folder_id);
    if (normalizeId(moveDestinationId) === destinationId) {
      setMoveDestinationId('');
      setMoveDestinationLabel('');
      setSuccess(`Cleared move destination for ${item.path || item.name || item.file_id}.`);
      setError('');
      return;
    }
    selectFolderForMoveDestination(item);
  }

  function toggleActionMenu(itemId) {
    setActionMenuKey((current) => (current === itemId ? '' : itemId));
  }

  function startRename(itemId) {
    setEditingKey(itemId);
    setActionMenuKey('');
  }

  function updateRenameDraft(itemId, value) {
    setRenameDrafts((current) => ({ ...current, [itemId]: value }));
  }

  function cancelRename(itemId) {
    setEditingKey('');
    setRenameNotices((current) => ({ ...current, [itemId]: null }));
  }

  if (authChecking) {
    return <LoginScreen authChecking />;
  }

  if (!authUser || !authToken) {
    return (
      <LoginScreen
        authChecking={false}
        authError={authError}
        loginUsername={loginUsername}
        loginPassword={loginPassword}
        loginSubmitting={loginSubmitting}
        onUsernameChange={(event) => setLoginUsername(event.target.value)}
        onPasswordChange={(event) => setLoginPassword(event.target.value)}
        onSubmit={handleLogin}
      />
    );
  }

  const headerProps = {
    authUser,
    viewMode,
    contentView,
    deleteMode,
    moveSelectionMode,
    purgeMode,
    refreshing,
    loading,
    onSwitchContentView: switchContentView,
    onToggleDeleteMode: toggleDeleteMode,
    onToggleMoveSelectionMode: toggleMoveSelectionMode,
    onTogglePurgeMode: togglePurgeMode,
    onToggleTrashView: toggleTrashView,
    onRefresh: refreshCurrentView,
    onLogout: handleLogout,
  };

  const treePanelProps = {
    tree: treeData,
    loading: treeLoading,
    error: treeError,
    currentFolderId,
    scope: treeScope,
    visibility: treeVisibility,
    onScopeChange: setTreeScope,
    onVisibilityChange: setTreeVisibility,
    onOpenFolder: handleOpenFolder,
  };

  const objectBrowserProps = {
    viewMode,
    visibleItems,
    totalItemCount,
    breadcrumbItems,
    currentFolderId,
    currentFolderState,
    searchQuery,
    sortMode,
    groupMode,
    loading,
    error,
    success,
    recentDeletes,
    deleteMode,
    visibleFileItems,
    selectedDeleteCount,
    trashAction,
    purgeMode,
    purgeFolderLabel,
    moveSelectionMode,
    moveSourceLabel,
    moveDestinationLabel,
    selectedTrashCount,
    selectedTrashIds,
    selectedDeleteIds,
    deleteConfirmKey,
    deleting,
    purgeFolderId: normalizeId(purgeFolderId),
    moveSourceId: normalizeId(moveSourceId),
    moveDestinationId: normalizeId(moveDestinationId),
    recentRenames,
    renameDrafts,
    actionMenuKey,
    editingKey,
    renameNotices,
    renaming,
    downloading,
    displayPath,
    formatDateTime,
    formatBytes,
    buildRenamedObjectName,
    getPreviewAvailability,
    previewLoadingId,
    dragDropActive,
    getMoveDestinationBlockReason,
    onDragEnter: handleUploadDragEnter,
    onDragOver: handleUploadDragOver,
    onDragLeave: handleUploadDragLeave,
    onDrop: handleUploadDrop,
    onOpenFolder: handleOpenFolder,
    onOpenParent: handleOpenParent,
    onSearchChange: (event) => setSearchQuery(event.target.value),
    onSortChange: (event) => setSortMode(event.target.value),
    onGroupChange: (event) => setGroupMode(event.target.value),
    onToggleSelectAllFilesForDelete: toggleSelectAllFilesForDelete,
    onSoftDeleteSelected: handleSoftDeleteSelected,
    onToggleSelectAllTrash: toggleSelectAllTrash,
    onRestoreTrash: handleRestoreTrash,
    onDeleteTrash: handleDeleteTrash,
    onToggleTrashSelection: toggleTrashSelection,
    onSelectOnlyTrash: selectOnlyTrashItem,
    onToggleDeleteSelection: toggleDeleteSelection,
    onDelete: handleDelete,
    onAskDelete: setDeleteConfirmKey,
    onCancelDelete: () => setDeleteConfirmKey(''),
    onTogglePurgeFolder: togglePurgeFolderSelection,
    onToggleMoveSource: toggleMoveSourceSelection,
    onToggleMoveDestination: toggleMoveDestinationSelection,
    onToggleActionMenu: toggleActionMenu,
    onStartRename: startRename,
    onRenameDraftChange: updateRenameDraft,
    onRename: handleRename,
    onCancelRename: cancelRename,
    onDownload: handleDownload,
    onPreview: handlePreview,
  };

  const sidebarProps = {
    viewMode,
    currentPath,
    folderForm: {
      value: folderName,
      submitting: creatingFolder,
      onChange: (event) => setFolderName(event.target.value),
      onSubmit: handleCreateFolder,
    },
    uploadForm: {
      selectedFile,
      replaceExisting: replaceExistingUpload,
      submitting: uploading || folderUploading,
      uploadEntries,
      formatBytes,
      onFileChange: handleSelectedFileChange,
      onReplaceExistingChange: (event) => setReplaceExistingUpload(event.target.checked),
      onSubmit: handleUpload,
    },
    folderUploadForm: {
      summary: selectedFolderUpload,
      submitting: folderUploading || uploading,
      formatBytes,
      onFolderChange: handleSelectedFolderChange,
      onSubmit: handleFolderUpload,
    },
    purgeControls: {
      folderId: purgeFolderId,
      folderLabel: purgeFolderLabel,
      state: purgeState,
      running: purging,
      onStart: () => handlePurge(),
      onResume: () => handlePurge({ resume: true }),
    },
    moveControls: {
      sourceId: moveSourceId,
      sourceLabel: moveSourceLabel,
      sourceKind: moveSourceKind,
      destinationId: moveDestinationId,
      destinationLabel: moveDestinationLabel,
      state: moveState,
      running: moveRunning,
      onUseCurrentFolderAsDestination: useCurrentFolderAsMoveDestination,
      onStartMerge: () => handleMoveOperation('merge'),
      onStartAvoidConflict: () => handleMoveOperation('avoid_conflict'),
      onResume: () => handleMoveOperation(moveState.mode || 'merge', { resume: true }),
    },
    trashControls: {
      selectedCount: selectedTrashCount,
      action: trashAction,
      onRestore: handleRestoreTrash,
      onDelete: handleDeleteTrash,
    },
    helpers: {
      buildFolderPreview,
      normalizeKey,
      normalizeId,
      getCurrentFolderMoveBlockReason,
    },
  };

  return (
    <>
      <WorkspaceShell
        headerProps={headerProps}
        showTreePanel={viewMode === 'files' && contentView === 'tree'}
        treePanelProps={treePanelProps}
        showObjectBrowser={viewMode === 'trash' || contentView === 'objects'}
        objectBrowserProps={objectBrowserProps}
        sidebarProps={sidebarProps}
      />
      <PreviewModal
        previewItem={previewItem}
        previewUrl={previewUrl}
        previewLines={previewLines}
        previewLanguage={previewLanguage}
        isPdfPreview={isPdfPreview}
        isTextPreview={isTextPreview}
        formatBytes={formatBytes}
        displayPath={displayPath}
        onClose={closePreview}
      />
    </>
  );
}
