export default function WorkspaceSidebar({
  viewMode,
  currentPath,
  folderForm,
  uploadForm,
  deleteById,
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
          <section className="panel side-panel">
            <div className="section-head">
              <h2>Create folder</h2>
            </div>
            <form className="stack-form" onSubmit={folderForm.onSubmit}>
              <label className="field">
                <span>Folder name</span>
                <input type="text" value={folderForm.value} onChange={folderForm.onChange} placeholder="new-folder" />
              </label>
              <div className="helper-text">Final folder: {buildFolderPreview(currentPath, folderForm.value) || '/'}</div>
              <button className="primary-button" type="submit" disabled={!normalizeKey(folderForm.value) || folderForm.submitting}>
                {folderForm.submitting ? 'Creating...' : 'Create folder'}
              </button>
            </form>
          </section>

          <section className="panel side-panel">
            <div className="section-head">
              <h2>Upload</h2>
            </div>
            <form className="stack-form" onSubmit={uploadForm.onSubmit}>
              <label className="field">
                <span>Select file</span>
                <input type="file" onChange={uploadForm.onFileChange} />
              </label>
              <div className="helper-text">Current folder: {currentPath || '/'}</div>
              <div className="helper-text">Maximum file size: 1GB</div>
              <button className="primary-button" type="submit" disabled={!uploadForm.selectedFile || uploadForm.submitting}>
                {uploadForm.submitting ? 'Uploading...' : 'Upload'}
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
                <input type="text" value={deleteById.value} onChange={deleteById.onChange} placeholder="uuid" />
              </label>
              <button className="danger-button" onClick={deleteById.onSubmit} disabled={!normalizeId(deleteById.value) || deleteById.submitting} type="button">
                {deleteById.submitting && normalizeId(deleteById.value) === deleteById.submitting ? 'Deleting...' : 'Delete'}
              </button>
            </div>
          </section>

          <section className="panel side-panel operation-tool-panel">
            <div className="section-head">
              <h2>Subtree purge</h2>
            </div>
            <div className="stack-form">
              <div className="operation-chip-row">
                <span className="operation-chip operation-chip-blue">DFS</span>
                <span className="operation-chip operation-chip-red">DB + S3 purge</span>
              </div>
              <label className="field">
                <span>Folder id</span>
                <input
                  type="text"
                  value={purgeControls.folderId}
                  onChange={purgeControls.onFolderIdChange}
                  placeholder="folder id"
                />
              </label>
              <div className="helper-text">
                Deletes the selected folder subtree permanently from DynamoDB and S3. Files and empty folders are marked
                `deletion_pending` before removal.
              </div>
              {purgeControls.folderLabel ? (
                <div className="operation-status">
                  <div>Selected folder: {purgeControls.folderLabel}</div>
                  <div>Selected id: {purgeControls.folderId}</div>
                </div>
              ) : null}
              {purgeControls.state.purge_active ? (
                <div className="operation-status">
                  <div>In progress: {purgeControls.state.root_id || 'unknown root'}</div>
                  <div>Phase: {purgeControls.state.phase || 'deleting'}</div>
                </div>
              ) : (
                <div className="operation-status operation-status-idle">No purge is active.</div>
              )}
              <button className="accent-danger-button" onClick={purgeControls.onStart} disabled={!normalizeId(purgeControls.folderId) || purgeControls.running} type="button">
                {purgeControls.running ? 'Deleting subtree...' : 'Start subtree purge'}
              </button>
              <button className="secondary-button" onClick={purgeControls.onResume} disabled={!purgeControls.state.purge_active || purgeControls.running} type="button">
                {purgeControls.running ? 'Resuming...' : 'Resume pending purge'}
              </button>
            </div>
          </section>

          <section className="panel side-panel operation-tool-panel">
            <div className="section-head">
              <h2>Move</h2>
            </div>
            <div className="stack-form">
              <div className="operation-chip-row">
                <span className="operation-chip operation-chip-blue">DFS copy</span>
                <span className="operation-chip operation-chip-red">Move + purge</span>
              </div>
              <label className="field">
                <span>Source id</span>
                <input
                  type="text"
                  value={moveControls.sourceId}
                  onChange={moveControls.onSourceIdChange}
                  placeholder="file or folder id"
                />
              </label>
              <label className="field">
                <span>Destination folder id</span>
                <input
                  type="text"
                  value={moveControls.destinationId}
                  onChange={moveControls.onDestinationIdChange}
                  placeholder="folder id or empty for root"
                />
              </label>
              <div className="helper-text">
                Mark source and destination from the list, or type the ids directly. Source can be a file or folder.
              </div>
              <button
                className="secondary-button"
                onClick={moveControls.onUseCurrentFolderAsDestination}
                disabled={!!getCurrentFolderMoveBlockReason()}
                type="button"
              >
                {getCurrentFolderMoveBlockReason() ? `${getCurrentFolderMoveBlockReason()} destination` : 'Use current folder as destination'}
              </button>
              {moveControls.sourceLabel ? (
                <div className="operation-status">
                  <div>Source: {moveControls.sourceLabel}</div>
                  <div>Kind: {moveControls.sourceKind || 'unknown'}</div>
                  <div>Id: {moveControls.sourceId}</div>
                </div>
              ) : null}
              {moveControls.destinationLabel ? (
                <div className="operation-status">
                  <div>Destination: {moveControls.destinationLabel}</div>
                  <div>Id: {moveControls.destinationId || '__root__'}</div>
                </div>
              ) : null}
              {moveControls.state.move_active ? (
                <div className="operation-status">
                  <div>In progress: {moveControls.state.source_id || 'unknown source'}</div>
                  <div>Kind: {moveControls.state.source_kind || 'unknown'}</div>
                  <div>Mode: {moveControls.state.mode || 'merge'}</div>
                  <div>Phase: {moveControls.state.phase || 'copying'}</div>
                </div>
              ) : (
                <div className="operation-status operation-status-idle">No move is active.</div>
              )}
              <button className="accent-danger-button" onClick={moveControls.onStartMerge} disabled={!normalizeId(moveControls.sourceId) || moveControls.running} type="button">
                {moveControls.running ? 'Moving...' : 'Start merge move'}
              </button>
              <button className="secondary-button" onClick={moveControls.onStartAvoidConflict} disabled={!normalizeId(moveControls.sourceId) || moveControls.running} type="button">
                {moveControls.running ? 'Preparing...' : 'Start avoid-conflict move'}
              </button>
              <button className="secondary-button" onClick={moveControls.onResume} disabled={!moveControls.state.move_active || moveControls.running} type="button">
                {moveControls.running ? 'Resuming...' : 'Resume pending move'}
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
            <button className="secondary-button" onClick={trashControls.onRestore} disabled={trashControls.selectedCount === 0 || !!trashControls.action} type="button">
              {trashControls.action === 'restore' ? 'Restoring...' : `Restore selected (${trashControls.selectedCount})`}
            </button>
            <button className="danger-button" onClick={trashControls.onDelete} disabled={trashControls.selectedCount === 0 || !!trashControls.action} type="button">
              {trashControls.action === 'delete' ? 'Deleting...' : `Delete forever (${trashControls.selectedCount})`}
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
  );
}
