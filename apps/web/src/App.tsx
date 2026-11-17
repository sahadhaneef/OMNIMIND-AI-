import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, clearSession, login, message, request, restore,
  type Organization, type Page, type User, type Workspace } from './api';

const roleNames: Record<string, string> = {
  organization_admin: 'Organization admin', knowledge_manager: 'Knowledge manager',
  employee: 'Employee', platform_operator: 'Platform operator',
};

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [booting, setBooting] = useState(true);
  const [notice, setNotice] = useState('');
  useEffect(() => {
    let active = true;
    restore().then(u => { if (active) setUser(u); }).catch(error => {
      if (active && !(error instanceof ApiError && error.status === 401)) setNotice(message(error));
    }).finally(() => { if (active) setBooting(false); });
    return () => { active = false; };
  }, []);
  function signedOut(note = '') { clearSession(); setUser(null); setNotice(note); }
  if (booting) return <main className="center" role="status">Opening your workspace…</main>;
  if (!user) return <Login notice={notice} onLogin={u => { setNotice(''); setUser(u); }} />;
  return <Shell key={user.id} user={user} onSignedOut={signedOut} />;
}

function Brand() { return <div className="brand"><span className="brand-icon">◎</span> OMNIMIND <span className="text-slate-400 font-normal">AI</span></div>; }

function Login({ notice, onLogin }: { notice: string; onLogin: (u: User) => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(''); setBusy(true);
    try { onLogin(await login(email, password)); }
    catch (e) { setPassword(''); setError(message(e)); }
    finally { setBusy(false); }
  }
  return <main className="min-h-screen grid lg:grid-cols-2">
    <section className="hidden lg:flex bg-slate-950 text-white p-16 flex-col justify-between">
      <Brand /><div><p className="eyebrow text-teal-300">KNOWLEDGE, CONNECTED.</p>
      <h1 className="text-5xl leading-tight font-semibold max-w-lg mt-6">A shared space.<br />A clearer perspective.</h1>
      <p className="text-slate-400 mt-6 max-w-sm leading-relaxed">Bring your organization together in secure, focused workspaces.</p></div>
      <p className="text-sm text-slate-500">Your knowledge starts with the right access.</p>
    </section>
    <section className="flex items-center justify-center p-6 sm:p-16">
      <div className="w-full max-w-sm"><div className="lg:hidden mb-12"><Brand /></div>
      <p className="eyebrow text-teal-700">WELCOME BACK</p><h2 className="text-3xl font-semibold mt-3">Sign in to OMNIMIND</h2>
      <p className="text-slate-500 mt-3 mb-8">Use your organization account to continue.</p>
      {(error || notice) && <p role="alert" className="error mb-6">{error || notice}</p>}
      <form onSubmit={submit} className="space-y-5">
        <label>Email address<input type="email" autoComplete="username" required maxLength={254} value={email} onChange={e => setEmail(e.target.value)} placeholder="you@organization.com" /></label>
        <label>Password<input type="password" autoComplete="current-password" required maxLength={1024} value={password} onChange={e => setPassword(e.target.value)} /></label>
        <button className="primary w-full" disabled={busy}>{busy ? 'Signing in…' : 'Sign in →'}</button>
      </form><p className="text-sm text-slate-500 mt-8">Need access? Contact your organization administrator.</p>
      </div>
    </section>
  </main>;
}

function Shell({ user, onSignedOut }: { user: User; onSignedOut: (note?: string) => void }) {
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [selected, setSelected] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [loggingOut, setLoggingOut] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true); setError('');
    request<Organization[]>('/organizations').then(rows => {
      if (active) { setOrganizations(rows); setSelected(rows[0]?.id ?? ''); }
    }).catch(e => {
      if (active) { if (e instanceof ApiError && e.status === 401) onSignedOut(message(e)); else setError(message(e)); }
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
    // onSignedOut only writes root state; reload is explicitly controlled by attempt.
  }, [attempt]);
  async function logout() {
    setLoggingOut(true); setError('');
    try { await request('/auth/logout', 'POST'); onSignedOut(); }
    catch (e) { if (e instanceof ApiError && e.status === 401) onSignedOut(); else setError(message(e)); }
    finally { setLoggingOut(false); }
  }
  const organization = organizations.find(o => o.id === selected);
  return <div className="min-h-screen md:grid md:grid-cols-[260px_1fr]">
    <aside className="bg-white border-r border-slate-200 p-6 flex flex-col md:min-h-screen gap-9">
      <Brand /><div><p className="eyebrow text-slate-400 mb-3">ORGANIZATION</p>
      <label className="sr-only" htmlFor="organization">Organization</label>
      <select id="organization" value={selected} onChange={e => setSelected(e.target.value)} disabled={loading || !organizations.length}>
        {!organizations.length && <option value="">No organizations</option>}
        {organizations.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
      </select>{organization && <p className="text-xs text-slate-500 mt-3">{roleNames[organization.role]}</p>}</div>
      <div className="rounded-lg bg-teal-50 text-teal-900 px-4 py-3 font-medium">▦ <span className="ml-2">Workspaces</span></div>
      <div className="md:mt-auto border-t border-slate-100 pt-5"><p className="text-sm break-all text-slate-600">{user.email}</p>
      <button onClick={logout} disabled={loggingOut} className="text-sm mt-3 text-slate-500 hover:text-slate-900">{loggingOut ? 'Signing out…' : 'Sign out ↗'}</button></div>
    </aside>
    <main className="p-6 lg:p-12 max-w-7xl w-full mx-auto">
      <header className="text-sm text-slate-500 border-b border-slate-200 pb-6 mb-10">{organization?.name ?? 'Your organization'} <span className="mx-3 text-slate-300">/</span> <span className="text-slate-800">Workspaces</span></header>
      {error && <div role="alert" className="error mb-6">{error} <button className="underline" onClick={() => setAttempt(a => a + 1)}>Retry</button></div>}
      {loading ? <p role="status">Loading organizations…</p> : organization ?
        <WorkspaceList key={organization.id} organization={organization} onExpired={() => onSignedOut('Your session expired. Please sign in again.')} /> :
        !error && <div className="empty"><h1 className="text-2xl font-semibold">No organization access yet</h1><p className="mt-3 text-slate-500">Ask your administrator to add you to an organization.</p></div>}
    </main>
  </div>;
}

function WorkspaceList({ organization, onExpired }: { organization: Organization; onExpired: () => void }) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [denied, setDenied] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const alive = useRef(true);
  const canCreate = ['organization_admin', 'knowledge_manager'].includes(organization.role) && !denied;
  function handleError(e: unknown) {
    if (e instanceof ApiError && e.status === 401) { onExpired(); return; }
    if (e instanceof ApiError && [403, 404].includes(e.status)) {
      setWorkspaces([]); setCursor(null); setDenied(true); setCreating(false); setName('');
    }
    setError(message(e));
  }
  useEffect(() => {
    alive.current = true;
    let active = true;
    setBusy(true); setError('');
    request<Page>(`/workspaces?organization_id=${organization.id}`).then(page => {
      if (active) { setWorkspaces(page.items); setCursor(page.next_cursor); setDenied(false); }
    }).catch(e => { if (active) handleError(e); }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; alive.current = false; };
  }, [organization.id, attempt]);
  async function more() {
    setBusy(true); setError('');
    try { const page = await request<Page>(`/workspaces?organization_id=${organization.id}&after=${cursor}`);
      if (alive.current) { setWorkspaces(old => [...old, ...page.items]); setCursor(page.next_cursor); }
    } catch (e) { if (alive.current) handleError(e); }
    finally { if (alive.current) setBusy(false); }
  }
  async function create(event: FormEvent) {
    event.preventDefault(); setSaving(true); setError('');
    try { const workspace = await request<Workspace>('/workspaces', 'POST', { organization_id: organization.id, name: name.trim() });
      if (alive.current) { setWorkspaces(old => [workspace, ...old]); setCreating(false); setName(''); }
    } catch (e) { if (alive.current) handleError(e); }
    finally { if (alive.current) setSaving(false); }
  }
  return <>
    <div className="flex flex-wrap items-start justify-between gap-6 mb-9"><div><p className="eyebrow text-teal-700">YOUR KNOWLEDGE, ORGANIZED</p>
      <h1 className="text-3xl font-semibold mt-3 tracking-tight">Workspaces</h1><p className="mt-3 text-slate-500">Focused spaces for your team’s knowledge.</p></div>
      {canCreate && <button className="primary" onClick={() => setCreating(true)}>＋ New workspace</button>}
    </div>
    {error && <div role="alert" className="error mb-6">{error} <button className="underline" onClick={() => setAttempt(a => a + 1)}>Refresh access</button></div>}
    {creating && <form onSubmit={create} className="bg-white border border-teal-200 rounded-xl p-6 mb-6">
      <h2 className="font-semibold text-lg mb-4">Create a workspace</h2>
      <label>Workspace name<input autoFocus required maxLength={120} value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Product research" /></label>
      <div className="flex gap-3 mt-5"><button className="primary" disabled={saving || !name.trim()}>{saving ? 'Creating…' : 'Create workspace'}</button>
      <button type="button" className="secondary" disabled={saving} onClick={() => { setCreating(false); setName(''); }}>Cancel</button></div>
    </form>}
    {busy && <p role="status" className="mb-5 text-slate-500">Loading workspaces…</p>}
    {!busy && !workspaces.length && !error && <div className="empty"><div className="text-4xl text-teal-700 mb-5">▦</div><h2 className="text-xl font-semibold">A place to start</h2>
      <p className="text-slate-500 mt-3">{canCreate ? 'Create your first workspace to bring your team together.' : 'No workspaces have been shared with you yet. Contact your administrator for access.'}</p></div>}
    <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-5">{workspaces.map(w => <article key={w.id} className="bg-white rounded-xl border border-slate-200 p-6">
      <div className="w-11 h-11 rounded-lg bg-teal-50 text-teal-800 flex items-center justify-center text-xl mb-6">▦</div>
      <h2 className="font-semibold text-lg break-words">{w.name}</h2><p className="text-xs text-slate-500 mt-5 border-t pt-4">{organization.name}</p>
    </article>)}</div>
    {cursor && <button className="secondary mt-6" onClick={more} disabled={busy}>Load more</button>}
  </>;
}
