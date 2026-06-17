export default function WorkspaceSidebar({
  viewMode,
  currentPath,
  folderForm,
  uploadForm,
  purgeControls,
  moveControls,
  trashControls,
  helpers,
}) {
  const { buildFolderPreview, normalizeKey, normalizeId, getCurrentFolderMoveBlockReason } = helpers;

  return (
    <aside className="side-column">
      {viewMode === 'files' ? (
        <>
          <section className="panel side-panel sidebar-panel">
            <div className="section-head">
              <h2>New folder</h2>
            </div>
            <form className="stack-form" onSubmit={folderForm.onSubmit}>
              <label className="field">
                <span>Folder name</span>
                <input type="text" value={folderForm.value} onChange={folderForm.onChange} placeholder="new-folder" />
              </label>
              <div className="sidebar-note">Path: {buildFolderPreview(currentPath, folderForm.value) || '/'}</div>
              <button className="primary-button" type="submit" disabled={!normalizeKey(folderForm.value) || folderForm.submitting}>
                {folderForm.submitting ? 'Creating...' : 'Create'}
              </button>
            </form>
          </section>

          <section className="panel side-panel sidebar-panel">
            <div className="section-head">
              <h2>Upload</h2>
            </div>
            <form className="stack-form" onSubmit={uploadForm.onSubmit}>
              <label className="field">
                <span>Select file</span>
                <input type="file" onChange={uploadForm.onFileChange} />
              </label>
              <div className="sidebar-note">To: {currentPath || '/'}</div>
              <div className="sidebar-note">Limit: 1GB</div>
              <button className="primary-button" type="submit" disabled={!uploadForm.selectedFile || uploadForm.submitting}>
                {uploadForm.submitting ? 'Uploading...' : 'Upload'}
              </button>
            </form>
          </section>

          <section className="panel side-panel operation-tool-panel sidebar-panel sidebar-panel-purge">
            <div className="section-head">
              <h2>Purge folder</h2>
            </div>
            <div className="stack-form">
              <div className="sidebar-note">Turn on purge mode, then select one folder from the list.</div>
              {purgeControls.folderLabel ? (
                <div className="sidebar-status-card sidebar-status-card-purge">
                  <div className="sidebar-status-title">Selected folder</div>
                  <div>{purgeControls.folderLabel}</div>
                  <div className="sidebar-note">{purgeControls.folderId}</div>
                </div>
              ) : (
                <div className="sidebar-status-card sidebar-status-idle">No folder selected yet.</div>
              )}
              {purgeControls.state.purge_active ? (
                <div className="sidebar-status-card sidebar-status-card-purge">
                  <div className="sidebar-status-title">Purge running</div>
                  <div>{purgeControls.state.root_id || 'unknown root'}</div>
                  <div className="sidebar-note">Phase: {purgeControls.state.phase || 'deleting'}</div>
                </div>
              ) : (
                <div className="sidebar-status-card sidebar-status-idle">Idle</div>
              )}
              <button className="danger-button" onClick={purgeControls.onStart} disabled={!normalizeId(purgeControls.folderId) || purgeControls.running} type="button">
                {purgeControls.running ? 'Purging...' : 'Start purge'}
              </button>
              <button className="secondary-button" onClick={purgeControls.onResume} disabled={!purgeControls.state.purge_active || purgeControls.running} type="button">
                {purgeControls.running ? 'Resuming...' : 'Resume'}
              </button>
            </div>
          </section>

          <section className="panel side-panel operation-tool-panel sidebar-panel sidebar-panel-move">
            <div className="section-head">
              <h2>Move</h2>
            </div>
            <div className="stack-form">
              <div className="sidebar-note">Turn on move mode, then pick one source and one destination from the list.</div>
              <button
                className="secondary-button"
                onClick={moveControls.onUseCurrentFolderAsDestination}
                disabled={!!getCurrentFolderMoveBlockReason()}
                type="button"
              >
                {getCurrentFolderMoveBlockReason() ? getCurrentFolderMoveBlockReason() : 'Use current folder'}
              </button>
              <div className="sidebar-status-grid">
                {moveControls.sourceLabel ? (
                  <div className="sidebar-status-card sidebar-status-card-move">
                    <div className="sidebar-status-title">Source</div>
                    <div>{moveControls.sourceLabel}</div>
                    <div className="sidebar-note">{`${moveControls.sourceKind || 'unknown'} | ${moveControls.sourceId}`}</div>
                  </div>
                ) : (
                  <div className="sidebar-status-card sidebar-status-idle">No source selected yet.</div>
                )}
                {moveControls.destinationLabel ? (
                  <div className="sidebar-status-card sidebar-status-card-move">
                    <div className="sidebar-status-title">Destination</div>
                    <div>{moveControls.destinationLabel}</div>
                    <div className="sidebar-note">{moveControls.destinationId || '__root__'}</div>
                  </div>
                ) : (
                  <div className="sidebar-status-card sidebar-status-idle">No destination selected yet.</div>
                )}
              </div>
              {moveControls.state.move_active ? (
                <div className="sidebar-status-card sidebar-status-card-move">
                  <div className="sidebar-status-title">Move running</div>
                  <div>{moveControls.state.source_id || 'unknown source'}</div>
                  <div className="sidebar-note">{`${moveControls.state.source_kind || 'unknown'} | ${moveControls.state.mode || 'merge'} | ${moveControls.state.phase || 'copying'}`}</div>
                </div>
              ) : (
                <div className="sidebar-status-card sidebar-status-idle">Idle</div>
              )}
              <div className="sidebar-button-stack">
                <button className="primary-button" onClick={moveControls.onStartMerge} disabled={!normalizeId(moveControls.sourceId) || moveControls.running} type="button">
                  {moveControls.running ? 'Moving...' : 'Merge move'}
                </button>
                <button className="secondary-button" onClick={moveControls.onStartAvoidConflict} disabled={!normalizeId(moveControls.sourceId) || moveControls.running} type="button">
                  {moveControls.running ? 'Preparing...' : 'Avoid conflict'}
                </button>
                <button className="secondary-button" onClick={moveControls.onResume} disabled={!moveControls.state.move_active || moveControls.running} type="button">
                  {moveControls.running ? 'Resuming...' : 'Resume'}
                </button>
              </div>
            </div>
          </section>
        </>
      ) : (
        <section className="panel side-panel sidebar-panel">
          <div className="section-head">
            <h2>Trash actions</h2>
          </div>
          <div className="stack-form">
            <div className="sidebar-note">Restore goes back to the original folder when possible.</div>
            <button className="secondary-button" onClick={trashControls.onRestore} disabled={trashControls.selectedCount === 0 || !!trashControls.action} type="button">
              {trashControls.action === 'restore' ? 'Restoring...' : `Restore selected (${trashControls.selectedCount})`}
            </button>
            <button className="danger-button" onClick={trashControls.onDelete} disabled={trashControls.selectedCount === 0 || !!trashControls.action} type="button">
              {trashControls.action === 'delete' ? 'Deleting...' : `Delete forever (${trashControls.selectedCount})`}
            </button>
          </div>
        </section>
      )}

      <section className="panel side-panel panel-muted sidebar-panel sidebar-session-panel">
        <p className="subtle panel-note">Your login stays active after reload until you log out or the token expires.</p>
      </section>
    </aside>
  );
}
