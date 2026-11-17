export type User = { id: string; email: string };
export type Organization = { id: string; name: string; role: string };
export type Workspace = { id: string; name: string; organization_id: string };
export type Page = { items: Workspace[]; next_cursor: string | null };

export class ApiError extends Error {
  constructor(public status: number, public code: string) { super(code); }
}

let csrfToken = '';
export function clearSession() { csrfToken = ''; }
export async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method, credentials: 'same-origin',
    headers: { ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...(method !== 'GET' && csrfToken ? { 'X-CSRF-Token': csrfToken } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ code: 'request_failed' })) as { code?: string };
    throw new ApiError(response.status, error.code ?? 'request_failed');
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export async function restore(): Promise<User> {
  const user = await request<User>('/auth/me');
  const csrf = await request<{ csrf_token: string }>('/auth/csrf');
  csrfToken = csrf.csrf_token;
  return user;
}
export async function login(email: string, password: string): Promise<User> {
  const user = await request<User & { csrf_token: string }>('/auth/login', 'POST', { email, password });
  csrfToken = user.csrf_token;
  return { id: user.id, email: user.email };
}
export function message(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'invalid_credentials') return 'Email or password is incorrect.';
    if (error.status === 401) return 'Your session expired. Please sign in again.';
    if (error.status === 429) return 'Too many sign-in attempts. Please try again in 15 minutes.';
    if (error.status === 403) return 'You do not have permission for this action. Refresh to update your access.';
    if (error.status === 404) return 'This organization or workspace is no longer accessible.';
    if (error.status === 409) return 'A workspace with this name already exists.';
    if (error.status === 422) return 'Check your entries and try again.';
  }
  return 'Unable to connect. Please try again.';
}
