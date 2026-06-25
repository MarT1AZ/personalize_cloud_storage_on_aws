import { useEffect, useState } from 'react';

function buildNodeKey(node) {
  return node?.id || '__root__';
}

function collectExpandedFolderKeys(node, next = {}) {
  if (!node || node.kind !== 'folder') {
    return next;
  }

  next[buildNodeKey(node)] = true;
  (node.children || []).forEach((child) => {
    if (child.kind === 'folder') {
      collectExpandedFolderKeys(child, next);
    }
  });
  return next;
}

function TreeNode({
  node,
  depth,
  expandedFolders,
  currentFolderId,
  onToggleFolder,
  onOpenFolder,
  ancestorGuides = [],
  isLastChild = true,
}) {
  const nodeKey = buildNodeKey(node);
  const isFolder = node.kind === 'folder';
  const isExpanded = !!expandedFolders[nodeKey];
  const isCurrentFolder = isFolder && (node.id || '') === (currentFolderId || '');
  const isDeleted = node.status === 'deleted';
  const uploadStateTag = node.upload_state === 'repair_required'
    ? 'Needs repair'
    : node.upload_state === 'finalizing'
      ? 'Finalizing'
      : node.upload_state === 'uploading'
        ? 'Uploading'
        : '';
  const hasChildren = Array.isArray(node.children) && node.children.length > 0;
  const showOwnGuide = depth > 0;

  return (
    <li className={`tree-node depth-${Math.min(depth, 6)}${isCurrentFolder ? ' tree-node-current' : ''}${isDeleted ? ' tree-node-deleted' : ''}`}>
      <div className="tree-node-row">
        <div className="tree-guide-gutter" aria-hidden="true">
          {ancestorGuides.map((hasContinuation, index) => (
            <span
              key={`guide-${nodeKey}-${index}`}
              className={`tree-guide-column${hasContinuation ? ' tree-guide-column-active' : ''}`}
            />
          ))}
          {showOwnGuide ? (
            <span className={`tree-guide-branch${isLastChild ? ' tree-guide-branch-last' : ' tree-guide-branch-mid'}`} />
          ) : null}
        </div>

        {isFolder ? (
          <button
            className="tree-expander"
            onClick={() => onToggleFolder(nodeKey)}
            disabled={!hasChildren}
            type="button"
          >
            {hasChildren ? (isExpanded ? '-' : '+') : '.'}
          </button>
        ) : (
          <span className="tree-file-dot" aria-hidden="true">•</span>
        )}

        {isFolder ? (
          <button
            className={`tree-node-button${isCurrentFolder ? ' tree-node-button-current' : ''}${isDeleted ? ' tree-node-button-deleted' : ''}`}
            onClick={() => onOpenFolder(node.id || '')}
            disabled={isDeleted}
            type="button"
          >
            <span className="tree-folder-symbol" aria-hidden="true">📁</span>
            <span className="tree-node-name">{node.name}</span>
            {isCurrentFolder ? <span className="tree-node-badge tree-node-badge-current">Current</span> : null}
            {isDeleted ? <span className="tree-node-badge tree-node-badge-deleted">Deleted</span> : null}
            {uploadStateTag ? <span className="tree-node-badge tree-node-badge-warning">{uploadStateTag}</span> : null}
          </button>
        ) : (
          <div className={`tree-node-file${isDeleted ? ' tree-node-file-deleted' : ''}`}>
            <span className="tree-file-symbol" aria-hidden="true">•</span>
            <span className="tree-node-name">{node.name}</span>
            {isDeleted ? <span className="tree-node-badge tree-node-badge-deleted">Deleted</span> : null}
          </div>
        )}
      </div>

      {isFolder && isExpanded && hasChildren ? (
        <ul className="tree-children">
          {node.children.map((child, index) => (
            <TreeNode
              key={`${child.kind}:${buildNodeKey(child)}`}
              node={child}
              depth={depth + 1}
              expandedFolders={expandedFolders}
              currentFolderId={currentFolderId}
              onToggleFolder={onToggleFolder}
              onOpenFolder={onOpenFolder}
              ancestorGuides={[...ancestorGuides, showOwnGuide && !isLastChild]}
              isLastChild={index === node.children.length - 1}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export default function TreePanel({
  tree,
  loading,
  error,
  currentFolderId,
  scope,
  visibility,
  onScopeChange,
  onVisibilityChange,
  onOpenFolder,
}) {
  const [expandedFolders, setExpandedFolders] = useState({});

  useEffect(() => {
    if (!tree) {
      setExpandedFolders({});
      return;
    }
    setExpandedFolders(collectExpandedFolderKeys(tree));
  }, [tree]);

  function toggleFolder(nodeKey) {
    setExpandedFolders((current) => ({
      ...current,
      [nodeKey]: !current[nodeKey],
    }));
  }

  return (
    <section className="panel tree-panel">
      <div className="section-head">
        <h2>Tree view</h2>
        <span className="count">{scope === 'current' ? 'Focused subtree' : 'Whole workspace'}</span>
      </div>

      <div className="tree-toolbar">
        <div className="tree-toggle-row">
          <button
            className={scope === 'root' ? 'accent-danger-button' : 'secondary-button'}
            onClick={() => onScopeChange('root')}
            type="button"
          >
            Root tree
          </button>
          <button
            className={scope === 'current' ? 'accent-danger-button' : 'secondary-button'}
            onClick={() => onScopeChange('current')}
            type="button"
          >
            Current folder
          </button>
        </div>
        <div className="tree-toggle-row">
          <button
            className={visibility === 'active' ? 'accent-danger-button' : 'secondary-button'}
            onClick={() => onVisibilityChange('active')}
            type="button"
          >
            Active only
          </button>
          <button
            className={visibility === 'all' ? 'accent-danger-button' : 'secondary-button'}
            onClick={() => onVisibilityChange('all')}
            type="button"
          >
            All states
          </button>
        </div>
      </div>

      <div className="sidebar-note tree-note">
        Click any active folder once to jump there. Deleted folders stay visible in `All states`, but they are not navigable.
      </div>

      {error ? <div className="error-box tree-message-box">{error}</div> : null}
      {loading ? <div className="empty-state tree-message-box">Loading tree...</div> : null}
      {!loading && !error && !tree ? <div className="empty-state tree-message-box">No tree data yet.</div> : null}

      {!loading && !error && tree ? (
        <div className="tree-shell">
          <ul className="tree-list">
            <TreeNode
              node={tree}
              depth={0}
              expandedFolders={expandedFolders}
              currentFolderId={currentFolderId}
              onToggleFolder={toggleFolder}
              onOpenFolder={onOpenFolder}
            />
          </ul>
        </div>
      ) : null}
    </section>
  );
}
