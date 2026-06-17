export default function WorkspaceHeader({
  authUser,
  viewMode,
  contentView,
  deleteMode,
  moveSelectionMode,
  purgeMode,
  refreshing,
  loading,
  onSwitchContentView,
  onToggleDeleteMode,
  onToggleMoveSelectionMode,
  onTogglePurgeMode,
  onToggleTrashView,
  onRefresh,
  onLogout,
}) {
  const trashActive = viewMode === 'trash';
  const filesViewActive = viewMode === 'files' && contentView === 'objects';
  const treeViewActive = viewMode === 'files' && contentView === 'tree';
  const fileActionDisabled = viewMode === 'trash' || contentView === 'tree';

  return (
    <section className="header-row">
      <div>
        <p className="eyebrow">Personal cloud storage</p>
        <h1>Files</h1>
        <p className="subtle">Browse folders, open nested paths, and manage files from one simple screen.</p>
      </div>
      <div className="header-actions">
        <div className="session-badge">Signed in as {authUser.username}</div>
        <button
          className={filesViewActive ? 'header-mode-button header-mode-button-active' : 'header-mode-button secondary-button'}
          onClick={() => onSwitchContentView('objects')}
          disabled={viewMode === 'trash'}
          type="button"
        >
          Objects
        </button>
        <button
          className={treeViewActive ? 'header-mode-button header-mode-button-active' : 'header-mode-button secondary-button'}
          onClick={() => onSwitchContentView('tree')}
          disabled={viewMode === 'trash'}
          type="button"
        >
          Tree
        </button>
        <button
          className={deleteMode ? 'header-mode-button header-mode-button-active' : 'header-mode-button secondary-button'}
          onClick={onToggleDeleteMode}
          disabled={fileActionDisabled}
          type="button"
        >
          {deleteMode ? 'Exit Delete' : 'Delete'}
        </button>
        <button
          className={moveSelectionMode ? 'header-mode-button header-mode-button-active' : 'header-mode-button secondary-button'}
          onClick={onToggleMoveSelectionMode}
          disabled={fileActionDisabled}
          type="button"
        >
          {moveSelectionMode ? 'Exit Move' : 'Move'}
        </button>
        <button
          className={purgeMode ? 'header-mode-button header-mode-button-active' : 'header-mode-button secondary-button'}
          onClick={onTogglePurgeMode}
          disabled={fileActionDisabled}
          type="button"
        >
          {purgeMode ? 'Exit Purge' : 'Purge'}
        </button>
        <button
          className={trashActive ? 'header-mode-button header-mode-button-active' : 'header-mode-button secondary-button'}
          onClick={onToggleTrashView}
          type="button"
        >
          {trashActive ? 'Exit Trash' : 'View Trash'}
        </button>
        <button className="secondary-button" onClick={onRefresh} disabled={refreshing || loading} type="button">
          {refreshing ? 'Refreshing...' : 'Refresh'}
        </button>
        <button className="ghost-button" onClick={onLogout} type="button">Log out</button>
      </div>
    </section>
  );
}
