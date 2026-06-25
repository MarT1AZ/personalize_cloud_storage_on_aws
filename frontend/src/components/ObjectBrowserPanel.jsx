function TrashRow({
  item,
  isSelected,
  onToggleTrashSelection,
  onSelectOnly,
  displayPath,
  formatDateTime,
}) {
  return (
    <li className="file-row" key={item.file_id}>
      <div className="file-meta">
        <div className="file-name-row">
          <label className="folder-open-button">
            <input
              type="checkbox"
              checked={isSelected}
              onChange={() => onToggleTrashSelection(item.file_id)}
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
        <button className="secondary-button" onClick={() => onSelectOnly(item.file_id)} type="button">
          Select
        </button>
      </div>
    </li>
  );
}

function FolderRow({
  item,
  deleteMode,
  deleteConfirmKey,
  deleting,
  purgeMode,
  purgeFolderId,
  moveSelectionMode,
  moveSourceId,
  moveDestinationId,
  moveDestinationBlockReason,
  onOpenFolder,
  onDelete,
  onAskDelete,
  onCancelDelete,
  onTogglePurgeFolder,
  onToggleMoveSource,
  onToggleMoveDestination,
}) {
  const isMoveSource = moveSelectionMode && moveSourceId === item.file_id;
  const isMoveDestination = moveSelectionMode && moveDestinationId === item.file_id;
  const uploadStateTag = item.upload_state === 'repair_required'
    ? 'Needs repair'
    : item.upload_state === 'finalizing'
      ? 'Finalizing'
      : item.upload_state === 'uploading'
        ? 'Uploading'
        : '';

  return (
    <li className={`file-row folder-row${isMoveSource ? ' move-source-row' : ''}${isMoveDestination ? ' move-destination-row' : ''}`} key={item.file_id}>
      <div className="folder-row-shell">
        <button className="folder-open-button" onClick={() => onOpenFolder(item.file_id)} type="button">
          <span className="folder-icon" aria-hidden="true">📁</span>
          <span className="folder-label">{item.name}</span>
          {uploadStateTag ? <span className="preview-tag preview-tag-warning">{uploadStateTag}</span> : null}
          <span className="folder-path">{item.path}</span>
        </button>
        {deleteMode ? (
          <div className="row-actions">
            {deleteConfirmKey === item.file_id ? (
              <>
                <button className="danger-button" onClick={() => onDelete(item.file_id)} disabled={deleting === item.file_id} type="button">
                  {deleting === item.file_id ? 'Deleting...' : 'Confirm'}
                </button>
                <button className="ghost-button" onClick={onCancelDelete} type="button">Cancel</button>
              </>
            ) : (
              <button className="danger-button" onClick={() => onAskDelete(item.file_id)} type="button">Delete</button>
            )}
          </div>
        ) : purgeMode ? (
          <div className="row-actions">
            <button
              className={purgeFolderId === item.file_id ? 'accent-danger-button' : 'secondary-button'}
              onClick={(event) => {
                event.stopPropagation();
                onTogglePurgeFolder(item);
              }}
              type="button"
            >
              {purgeFolderId === item.file_id ? 'Selected' : 'Select'}
            </button>
          </div>
        ) : moveSelectionMode ? (
          <div className="row-actions">
            <button
              className={isMoveSource ? 'accent-danger-button' : 'secondary-button'}
              onClick={(event) => {
                event.stopPropagation();
                onToggleMoveSource(item);
              }}
              type="button"
            >
              {isMoveSource ? 'Source' : 'Mark source'}
            </button>
            <button
              className={isMoveDestination ? 'accent-danger-button' : 'secondary-button'}
              onClick={(event) => {
                event.stopPropagation();
                onToggleMoveDestination(item);
              }}
              disabled={!isMoveDestination && !!moveDestinationBlockReason}
              type="button"
            >
              {isMoveDestination ? 'Destination' : moveDestinationBlockReason || 'Mark destination'}
            </button>
          </div>
        ) : null}
      </div>
    </li>
  );
}

function FileRow({
  item,
  deleteMode,
  selectedDeleteIds,
  deleteConfirmKey,
  deleting,
  moveSelectionMode,
  moveSourceId,
  recentRenames,
  renameDrafts,
  actionMenuKey,
  editingKey,
  renameNotices,
  renaming,
  downloading,
  formatBytes,
  formatDateTime,
  buildRenamedObjectName,
  getPreviewAvailability,
  previewLoadingId,
  onToggleDeleteSelection,
  onDelete,
  onAskDelete,
  onCancelDelete,
  onToggleMoveSource,
  onToggleActionMenu,
  onStartRename,
  onRenameDraftChange,
  onRename,
  onCancelRename,
  onDownload,
  onPreview,
}) {
  const isMoveSource = moveSelectionMode && moveSourceId === item.file_id;
  const renamePreview = buildRenamedObjectName(renameDrafts[item.file_id] || '', item.file_extension || '');
  const previewAvailability = getPreviewAvailability(item);

  return (
    <li
      className={`file-row${isMoveSource ? ' move-source-row' : ''}`}
      key={item.file_id}
      data-file-key={item.file_id}
      onContextMenu={(event) => {
        event.preventDefault();
        onToggleActionMenu(item.file_id);
      }}
    >
      <div className="file-meta">
        <div className="file-name-row">
          {deleteMode ? (
            <label className="folder-open-button">
              <input
                type="checkbox"
                checked={!!selectedDeleteIds[item.file_id]}
                onChange={() => onToggleDeleteSelection(item.file_id)}
              />
            </label>
          ) : null}
          <div className="file-name">{item.name || 'Unnamed file'}</div>
          {recentRenames[item.file_id] ? (
            <div className="rename-tag">
              {recentRenames[item.file_id].oldName} {'>>'} {recentRenames[item.file_id].newName}
            </div>
          ) : null}
          {!previewAvailability.canPreview ? (
            <div className="preview-tag preview-tag-blocked" title={previewAvailability.reason}>
              {previewAvailability.tag}
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
            <button className="secondary-button" onClick={() => onStartRename(item.file_id)} type="button">
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
                onChange={(event) => onRenameDraftChange(item.file_id, event.target.value)}
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
              <button className="secondary-button" onClick={() => onRename(item)} disabled={!renamePreview || renaming === item.file_id} type="button">
                {renaming === item.file_id ? 'Renaming...' : 'Rename'}
              </button>
              <button className="ghost-button" onClick={() => onCancelRename(item.file_id)} type="button">
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
              <button className="danger-button" onClick={() => onDelete(item.file_id)} disabled={deleting === item.file_id} type="button">
                {deleting === item.file_id ? 'Deleting...' : 'Confirm'}
              </button>
              <button className="ghost-button" onClick={onCancelDelete} type="button">Cancel</button>
            </>
          ) : (
            <button className="danger-button" onClick={() => onAskDelete(item.file_id)} type="button">Delete</button>
          )
        ) : null}
        {moveSelectionMode ? (
          <button
            className={isMoveSource ? 'accent-danger-button' : 'secondary-button'}
            onClick={() => onToggleMoveSource(item)}
            type="button"
          >
            {isMoveSource ? 'Source' : 'Mark source'}
          </button>
        ) : null}
        <button className="ghost-button" onClick={() => onToggleActionMenu(item.file_id)} type="button">
          Actions
        </button>
        {previewAvailability.canPreview ? (
          <button className="secondary-button" onClick={() => onPreview(item)} disabled={previewLoadingId === item.file_id} type="button">
            {previewLoadingId === item.file_id ? 'Opening...' : 'Preview'}
          </button>
        ) : null}
        <button className="secondary-button" onClick={() => onDownload(item.file_id)} disabled={downloading === item.file_id} type="button">
          {downloading === item.file_id ? 'Preparing...' : 'Download'}
        </button>
      </div>
    </li>
  );
}

export default function ObjectBrowserPanel(props) {
  const {
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
    purgeFolderId,
    moveSourceId,
    moveDestinationId,
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
    getMoveDestinationBlockReason,
    onOpenFolder,
    onOpenParent,
    onSearchChange,
    onSortChange,
    onGroupChange,
    onToggleSelectAllFilesForDelete,
    onSoftDeleteSelected,
    onToggleSelectAllTrash,
    onRestoreTrash,
    onDeleteTrash,
    onToggleTrashSelection,
    onSelectOnlyTrash,
    onToggleDeleteSelection,
    onDelete,
    onAskDelete,
    onCancelDelete,
    onTogglePurgeFolder,
    onToggleMoveSource,
    onToggleMoveDestination,
    onToggleActionMenu,
    onStartRename,
    onRenameDraftChange,
    onRename,
    onCancelRename,
    onDownload,
    onPreview,
  } = props;

  return (
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
              onClick={() => onOpenFolder(item.folder_id)}
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
          <input type="text" value={searchQuery} onChange={onSearchChange} placeholder="Search this folder" />
        </label>
        <label className="field control-field">
          <span>Sort</span>
          <select value={sortMode} onChange={onSortChange}>
            <option value="alpha-asc">Alphabet (A to Z)</option>
            <option value="alpha-desc">Alphabet (Z to A)</option>
            <option value="modified-newest">Newest modified</option>
            <option value="modified-oldest">Oldest modified</option>
          </select>
        </label>
        <label className="field control-field">
          <span>Group</span>
          <select value={groupMode} onChange={onGroupChange}>
            <option value="folder-first">Folder first</option>
            <option value="file-first">File first</option>
          </select>
        </label>
      </div>

      {error ? <div className="error-box">{error}</div> : null}
      {success ? <div className="success-box">{success}</div> : null}
      {viewMode === 'files' && currentFolderState?.upload_state ? (
        <div className="warning-box">
          {currentFolderState.upload_warning || `This folder is currently ${String(currentFolderState.upload_state).replace(/_/g, ' ')}.`}
        </div>
      ) : null}
      {recentDeletes.length > 0 ? (
        <div className="delete-history-box">
          <div className="delete-history-title">Recently deleted</div>
          <div className="delete-history-list">
            {recentDeletes.map((item) => <div className="delete-tag" key={item}>{item}</div>)}
          </div>
        </div>
      ) : null}
      {viewMode === 'files' && deleteMode ? (
        <div className="delete-history-box">
          <div className="delete-history-title">Delete selection</div>
          <div className="delete-history-list">
            <button className="secondary-button" onClick={onToggleSelectAllFilesForDelete} disabled={visibleFileItems.length === 0 || !!trashAction} type="button">
              {selectedDeleteCount === visibleFileItems.length && visibleFileItems.length > 0 ? 'Clear visible files' : 'Select visible files'}
            </button>
            <button className="danger-button" onClick={onSoftDeleteSelected} disabled={selectedDeleteCount === 0 || !!trashAction} type="button">
              {trashAction === 'soft-delete' ? 'Moving...' : `Move selected to trash (${selectedDeleteCount})`}
            </button>
          </div>
        </div>
      ) : null}
      {viewMode === 'files' && purgeMode ? (
        <div className="delete-history-box operation-box">
          <div className="delete-history-title">Purge selection</div>
          <div className="delete-history-list">
            <div className="helper-text">
              Click one folder row to select it for subtree purge. Turning purge off will clear the selection.
            </div>
            {purgeFolderLabel ? <div className="delete-tag">{purgeFolderLabel}</div> : null}
          </div>
        </div>
      ) : null}
      {viewMode === 'files' && moveSelectionMode ? (
        <div className="delete-history-box operation-box">
          <div className="delete-history-title">Move selection</div>
          <div className="delete-history-list">
            <div className="helper-text">
              Mark one file or folder as the source, then mark one folder or the current path as the destination.
            </div>
            {moveSourceLabel ? <div className="delete-tag">Source: {moveSourceLabel}</div> : null}
            {moveDestinationLabel ? <div className="delete-tag">Destination: {moveDestinationLabel}</div> : null}
          </div>
        </div>
      ) : null}
      {viewMode === 'trash' ? (
        <div className="delete-history-box">
          <div className="delete-history-title">Selection</div>
          <div className="delete-history-list">
            <button className="secondary-button" onClick={onToggleSelectAllTrash} disabled={visibleItems.length === 0 || !!trashAction} type="button">
              {selectedTrashCount === visibleItems.length && visibleItems.length > 0 ? 'Clear visible selection' : 'Select visible'}
            </button>
            <button className="secondary-button" onClick={onRestoreTrash} disabled={selectedTrashCount === 0 || !!trashAction} type="button">
              {trashAction === 'restore' ? 'Restoring...' : `Restore selected (${selectedTrashCount})`}
            </button>
            <button className="danger-button" onClick={onDeleteTrash} disabled={selectedTrashCount === 0 || !!trashAction} type="button">
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
              <button className="folder-open-button" onClick={onOpenParent} type="button">
                <span className="folder-icon" aria-hidden="true">📁</span>
                <span className="folder-label">..</span>
              </button>
            </li>
          ) : null}

          {visibleItems.map((item) => {
            if (viewMode === 'trash') {
              return (
                <TrashRow
                  key={item.file_id}
                  item={item}
                  isSelected={!!selectedTrashIds[item.file_id]}
                  onToggleTrashSelection={onToggleTrashSelection}
                  onSelectOnly={onSelectOnlyTrash}
                  displayPath={displayPath}
                  formatDateTime={formatDateTime}
                />
              );
            }

            if (item.kind === 'folder') {
              return (
                <FolderRow
                  key={item.file_id}
                  item={item}
                  deleteMode={deleteMode}
                  deleteConfirmKey={deleteConfirmKey}
                  deleting={deleting}
                  purgeMode={purgeMode}
                  purgeFolderId={purgeFolderId}
                  moveSelectionMode={moveSelectionMode}
                  moveSourceId={moveSourceId}
                  moveDestinationId={moveDestinationId}
                  moveDestinationBlockReason={getMoveDestinationBlockReason(item)}
                  onOpenFolder={onOpenFolder}
                  onDelete={onDelete}
                  onAskDelete={onAskDelete}
                  onCancelDelete={onCancelDelete}
                  onTogglePurgeFolder={onTogglePurgeFolder}
                  onToggleMoveSource={onToggleMoveSource}
                  onToggleMoveDestination={onToggleMoveDestination}
                />
              );
            }

            return (
              <FileRow
                key={item.file_id}
                item={item}
                deleteMode={deleteMode}
                selectedDeleteIds={selectedDeleteIds}
                deleteConfirmKey={deleteConfirmKey}
                deleting={deleting}
                moveSelectionMode={moveSelectionMode}
                moveSourceId={moveSourceId}
                recentRenames={recentRenames}
                renameDrafts={renameDrafts}
                actionMenuKey={actionMenuKey}
                editingKey={editingKey}
                renameNotices={renameNotices}
                renaming={renaming}
                downloading={downloading}
                formatBytes={formatBytes}
                formatDateTime={formatDateTime}
                buildRenamedObjectName={buildRenamedObjectName}
                getPreviewAvailability={getPreviewAvailability}
                previewLoadingId={previewLoadingId}
                onToggleDeleteSelection={onToggleDeleteSelection}
                onDelete={onDelete}
                onAskDelete={onAskDelete}
                onCancelDelete={onCancelDelete}
                onToggleMoveSource={onToggleMoveSource}
                onToggleActionMenu={onToggleActionMenu}
                onStartRename={onStartRename}
                onRenameDraftChange={onRenameDraftChange}
                onRename={onRename}
                onCancelRename={onCancelRename}
                onDownload={onDownload}
                onPreview={onPreview}
              />
            );
          })}
        </ul>
      )}
    </section>
  );
}
