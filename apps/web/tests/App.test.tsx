import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../src/App';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const a = { id: 'a', name: 'Alpha', role: 'organization_admin' };
const b = { id: 'b', name: 'Beta', role: 'employee' };
const user = { id: 'u', email: 'person@example.com' };
function response(body: unknown, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(body), { status });
}

describe('application flows', () => {
  it('signs in, creates a workspace, and invalidates the session on logout', async () => {
    let authenticated = false;
    let created = false;
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/auth/me')) return response({ code: 'session_expired' }, 401);
      if (url.endsWith('/auth/login')) { authenticated = true; return response({ ...user, csrf_token: 'token' }); }
      if (url.endsWith('/organizations')) return response([a]);
      if (url.includes('/workspaces?')) return response({ items: [], next_cursor: null });
      if (url.endsWith('/workspaces') && init?.method === 'POST') {
        expect(authenticated).toBe(true);
        expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('token');
        expect(JSON.parse(init.body as string)).toEqual({ organization_id: 'a', name: 'Research' });
        created = true;
        return response({ id: 'w', organization_id: 'a', name: 'Research' }, 201);
      }
      if (url.endsWith('/auth/logout')) { authenticated = false; return response(null, 204); }
      throw new Error(url);
    });
    vi.stubGlobal('fetch', fetch);
    render(<App />);
    await userEvent.type(await screen.findByLabelText('Email address'), user.email);
    await userEvent.type(screen.getByLabelText('Password'), 'my-password');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in →' }));
    await userEvent.click(await screen.findByRole('button', { name: /New workspace/ }));
    await userEvent.type(screen.getByLabelText('Workspace name'), 'Research');
    await userEvent.click(screen.getByRole('button', { name: 'Create workspace' }));
    expect(await screen.findByRole('heading', { name: 'Research' })).toBeTruthy();
    expect(created).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: /Sign out/ }));
    expect(await screen.findByLabelText('Password')).toBeTruthy();
    expect(authenticated).toBe(false);
    expect(screen.queryByText('Research')).toBeNull();
  });

  it('clears organization state and ignores a late response from the previous tenant', async () => {
    let resolveA: (value: Response) => void = () => {};
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.endsWith('/auth/me')) return response(user);
      if (url.endsWith('/auth/csrf')) return response({ csrf_token: 'token' });
      if (url.endsWith('/organizations')) return response([a, b]);
      if (url.includes('organization_id=a')) return new Promise<Response>(resolve => { resolveA = resolve; });
      if (url.includes('organization_id=b')) return response({ items: [{ id: 'wb', name: 'Beta workspace' }], next_cursor: null });
      throw new Error(url);
    }));
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: /New workspace/ }));
    await userEvent.type(screen.getByLabelText('Workspace name'), 'Alpha draft');
    await userEvent.selectOptions(screen.getByLabelText('Organization'), 'b');
    expect(await screen.findByText('Beta workspace')).toBeTruthy();
    expect(screen.queryByLabelText('Workspace name')).toBeNull();
    expect(screen.queryByRole('button', { name: /New workspace/ })).toBeNull();
    resolveA(response({ items: [{ id: 'wa', name: 'Alpha secret' }], next_cursor: null }));
    await waitFor(() => expect(screen.queryByText('Alpha secret')).toBeNull());
  });

  it('returns to login after session expiry', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.endsWith('/auth/me')) return response(user);
      if (url.endsWith('/auth/csrf')) return response({ csrf_token: 'token' });
      if (url.endsWith('/organizations')) return response([a]);
      return response({ code: 'session_expired' }, 401);
    }));
    render(<App />);
    expect(await screen.findByLabelText('Password')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('session expired');
  });

  it('clears workspace data when access is revoked', async () => {
    let denied = false;
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.endsWith('/auth/me')) return response(user);
      if (url.endsWith('/auth/csrf')) return response({ csrf_token: 'token' });
      if (url.endsWith('/organizations')) return response([a]);
      if (url.includes('after=')) { denied = true; return response({ code: 'organization_not_found' }, 404); }
      return response({ items: [{ id: 'w', name: 'Private workspace' }], next_cursor: 'w' });
    }));
    render(<App />);
    expect(await screen.findByText('Private workspace')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Load more' }));
    await screen.findByRole('alert');
    expect(denied).toBe(true);
    expect(screen.queryByText('Private workspace')).toBeNull();
    expect(screen.queryByRole('button', { name: /New workspace/ })).toBeNull();
  });
});
