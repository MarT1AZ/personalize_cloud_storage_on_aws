export default function PreviewModal({
  previewItem,
  previewUrl,
  previewLines,
  previewLanguage,
  isPdfPreview,
  isTextPreview,
  formatBytes,
  displayPath,
  onClose,
}) {
  if (!previewItem || !previewUrl) {
    return null;
  }

  return (
    <div className="preview-overlay" onClick={onClose} role="presentation">
      <section
        className="preview-modal"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`Preview ${previewItem.name || 'file'}`}
      >
        <div className="preview-head">
          <div>
            <div className="preview-title">{previewItem.name || 'Preview'}</div>
            <div className="preview-meta">{`${formatBytes(previewItem.size)} | ${displayPath(previewItem.path || '')}`}</div>
          </div>
          <button className="ghost-button" onClick={onClose} type="button">Close</button>
        </div>
        <div className="preview-body">
          {isPdfPreview(previewItem) ? (
            <iframe
              className="preview-document"
              src={previewUrl}
              title={previewItem.name || 'PDF preview'}
            />
          ) : isTextPreview(previewItem) ? (
            <div className="preview-code">
              <div className="preview-code-lines">
                {previewLines.map((line, index) => (
                  <div className="preview-code-line" key={`${previewItem.file_id}-${index + 1}`}>
                    <div className="preview-code-line-number">{index + 1}</div>
                    {previewLanguage === 'plain' ? (
                      <code className="preview-code-line-content">{line || ' '}</code>
                    ) : (
                      <code
                        className={`preview-code-line-content language-${previewLanguage}`}
                        dangerouslySetInnerHTML={{ __html: line || ' ' }}
                      />
                    )}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <img className="preview-image" src={previewUrl} alt={previewItem.name || 'Preview'} />
          )}
        </div>
      </section>
    </div>
  );
}
