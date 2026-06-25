import ObjectBrowserPanel from './ObjectBrowserPanel';
import TreePanel from './TreePanel';
import WorkspaceHeader from './WorkspaceHeader';
import WorkspaceSidebar from './WorkspaceSidebar';

export default function WorkspaceShell({
  headerProps,
  showTreePanel,
  treePanelProps,
  showObjectBrowser,
  objectBrowserProps,
  sidebarProps,
}) {
  return (
    <main className="app-shell">
      <WorkspaceHeader {...headerProps} />

      <section className="workspace-grid">
        <div className="main-column">
          {showTreePanel ? <TreePanel {...treePanelProps} /> : null}
          {showObjectBrowser ? <ObjectBrowserPanel {...objectBrowserProps} /> : null}
        </div>

        <WorkspaceSidebar {...sidebarProps} />
      </section>
    </main>
  );
}
