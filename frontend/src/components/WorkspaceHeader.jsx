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
  const buttonLabel = (icon, text) => (
    <span className="header-button-content">
      <span className="header-button-icon" aria-hidden="true">{icon}</span>
      <span>{text}</span>
    </span>
  );

  return (
    <section className="header-row">
      <div className="header-copy">
        <p className="eyebrow">Personal cloud storage</p>
        <h1>Files</h1>
        <p className="subtle">Browse folders, open nested paths, and manage files from one simple screen.</p>
      </div>
      <div className="header-actions">
        <div className="session-badge">Signed in as {authUser.username}</div>
        <div className="header-toolbar">
          <div className="header-action-group">
            <div className="header-group-label">Viewing</div>
            <div className="header-group-buttons">
              <button
                className={filesViewActive ? 'header-mode-button header-mode-button-active' : 'header-mode-button secondary-button'}
                onClick={() => onSwitchContentView('objects')}
                disabled={viewMode === 'trash'}
                type="button"
              >
                {buttonLabel('▣', 'Objects')}
              </button>
              <button
                className={treeViewActive ? 'header-mode-button header-mode-button-active' : 'header-mode-button secondary-button'}
                onClick={() => onSwitchContentView('tree')}
                disabled={viewMode === 'trash'}
                type="button"
              >
                {buttonLabel('▤', 'Tree')}
              </button>
            </div>
          </div>

          <div className="header-action-group header-action-group-critical">
            <div className="header-group-label">Move / Purge / Delete</div>
            <div className="header-group-buttons">
              <button
                className={deleteMode ? 'header-mode-button header-mode-button-active' : 'header-mode-button secondary-button'}
                onClick={onToggleDeleteMode}
                disabled={fileActionDisabled}
                type="button"
              >
                {buttonLabel(deleteMode ? '↩' : '✕', deleteMode ? 'Exit Delete' : 'Delete')}
              </button>
              <button
                className={moveSelectionMode ? 'header-mode-button header-mode-button-active' : 'header-mode-button secondary-button'}
                onClick={onToggleMoveSelectionMode}
                disabled={fileActionDisabled}
                type="button"
              >
                {buttonLabel(moveSelectionMode ? '↩' : '⇄', moveSelectionMode ? 'Exit Move' : 'Move')}
              </button>
              <button
                className={purgeMode ? 'header-mode-button header-mode-button-active' : 'header-mode-button secondary-button'}
                onClick={onTogglePurgeMode}
                disabled={fileActionDisabled}
                type="button"
              >
                {buttonLabel(purgeMode ? '↩' : '⚡', purgeMode ? 'Exit Purge' : 'Purge')}
              </button>
            </div>
          </div>

          <div className="header-action-group">
            <div className="header-group-label">Trash</div>
            <div className="header-group-buttons">
              <button
                className={trashActive ? 'header-mode-button header-mode-button-active' : 'header-mode-button secondary-button'}
                onClick={onToggleTrashView}
                type="button"
              >
                {buttonLabel(trashActive ? '↩' : '🗑', trashActive ? 'Exit Trash' : 'View Trash')}
              </button>
            </div>
          </div>

          <div className="header-action-group">
            <div className="header-group-label">Refresh</div>
            <div className="header-group-buttons">
              <button className="header-mode-button secondary-button" onClick={onRefresh} disabled={refreshing || loading} type="button">
                {buttonLabel(refreshing ? '⟳' : '↻', refreshing ? 'Refreshing...' : 'Refresh')}
              </button>
            </div>
          </div>
        </div>
        <button className="ghost-button header-logout-button" onClick={onLogout} type="button">
          {buttonLabel('⇥', 'Log out')}
        </button>
      </div>
    </section>
  );
}
