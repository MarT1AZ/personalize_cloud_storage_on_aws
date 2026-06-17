export default function LoginScreen({
  authChecking,
  authError,
  loginUsername,
  loginPassword,
  loginSubmitting,
  onUsernameChange,
  onPasswordChange,
  onSubmit,
}) {
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

  return (
    <main className="login-shell">
      <section className="panel login-card">
        <p className="eyebrow">Personal cloud storage</p>
        <h1>Log in</h1>
        <p className="subtle">Use your username and password to open the file manager.</p>
        {authError ? <div className="error-box">{authError}</div> : null}
        <form className="stack-form" onSubmit={onSubmit}>
          <label className="field">
            <span>Username</span>
            <input type="text" value={loginUsername} onChange={onUsernameChange} autoComplete="username" placeholder="marz" />
          </label>
          <label className="field">
            <span>Password</span>
            <input type="password" value={loginPassword} onChange={onPasswordChange} autoComplete="current-password" placeholder="Enter password" />
          </label>
          <button className="primary-button" type="submit" disabled={!loginUsername.trim() || !loginPassword || loginSubmitting}>
            {loginSubmitting ? 'Logging in...' : 'Log in'}
          </button>
        </form>
      </section>
    </main>
  );
}
