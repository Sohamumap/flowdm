import type {
  Account,
  AppConfig,
  Automation,
  AutomationIn,
  AutomationStats,
  CommentEvent,
  Contact,
  EmailSettings,
  EmailSettingsIn,
  InstagramAppSettings,
  InstagramAppSettingsIn,
  FailedJob,
  MediaPage,
  Message,
  Paginated,
  Run,
  Stats,
  User,
  WebhookLogEntry,
} from './types'

/** Error thrown for any failed API call; `message` is always human readable. */
export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(message: string, status: number, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

/** Readable message for anything caught in a try/catch. */
export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message
  if (typeof err === 'string') return err
  return 'Something went wrong'
}

// Endpoints where a 401 is an expected answer, not an expired session.
const NO_REDIRECT_ON_401 = ['/api/auth/me', '/api/auth/login']

function cleanMsg(msg: string): string {
  return msg.replace(/^Value error,\s*/i, '').trim()
}

/** Turn a FastAPI error body into one readable sentence. */
export function formatDetail(detail: unknown): string | null {
  if (detail == null) return null
  if (typeof detail === 'string') return cleanMsg(detail) || null
  if (Array.isArray(detail)) {
    const msgs: string[] = []
    for (const item of detail) {
      let msg: string | null = null
      if (typeof item === 'string') msg = item
      else if (item && typeof item === 'object' && 'msg' in item && typeof item.msg === 'string') msg = item.msg
      if (msg) {
        const cleaned = cleanMsg(msg)
        if (cleaned && !msgs.includes(cleaned)) msgs.push(cleaned)
      }
    }
    return msgs.length ? msgs.join('; ') : null
  }
  if (typeof detail === 'object' && 'msg' in detail && typeof detail.msg === 'string') {
    return cleanMsg(detail.msg)
  }
  return null
}

function redirectToLogin() {
  if (window.location.pathname !== '/login') {
    window.location.assign('/login')
  }
}

type Query = Record<string, string | number | boolean | null | undefined>

function withQuery(path: string, query?: Query): string {
  if (!query) return path
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue
    params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${path}?${qs}` : path
}

async function request<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  const url = withQuery(path, query)
  const headers: Record<string, string> = { Accept: 'application/json' }
  const init: RequestInit = { method, credentials: 'include', headers }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }

  let res: Response
  try {
    res = await fetch(url, init)
  } catch {
    throw new ApiError('Cannot reach the server. Check your connection and try again.', 0)
  }

  if (res.status === 204) return undefined as T

  const text = await res.text()
  let data: unknown = undefined
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }

  if (!res.ok) {
    if (res.status === 401 && !NO_REDIRECT_ON_401.some((p) => path.startsWith(p))) {
      redirectToLogin()
    }
    const detail = data && typeof data === 'object' && 'detail' in data ? (data as { detail: unknown }).detail : undefined
    let message = formatDetail(detail)
    if (!message) {
      if (res.status === 401) message = 'Please log in again.'
      else if (res.status === 403) message = 'You do not have access to this.'
      else if (res.status === 404) message = 'Not found.'
      else if (res.status >= 500) message = `Server error (${res.status}). Check the backend logs.`
      else message = `Request failed (${res.status}${res.statusText ? ` ${res.statusText}` : ''})`
    }
    throw new ApiError(message, res.status, detail)
  }

  return data as T
}

const get = <T>(path: string, query?: Query) => request<T>('GET', path, undefined, query)
const post = <T>(path: string, body?: unknown) => request<T>('POST', path, body)
const put = <T>(path: string, body?: unknown) => request<T>('PUT', path, body)
const patch = <T>(path: string, body?: unknown) => request<T>('PATCH', path, body)
const del = (path: string) => request<void>('DELETE', path)

export type PageQuery = {
  limit?: number
  offset?: number
}

export const api = {
  auth: {
    login: (email: string, password: string) => post<User>('/api/auth/login', { email, password }),
    logout: () => post<void>('/api/auth/logout'),
    me: () => get<User>('/api/auth/me'),
    changePassword: (current_password: string, new_password: string) =>
      post<void>('/api/auth/change-password', { current_password, new_password }),
  },

  config: () => get<AppConfig>('/api/config'),

  settings: {
    instagramApp: () => get<InstagramAppSettings>('/api/settings/instagram'),
    updateInstagramApp: (body: InstagramAppSettingsIn) => put<InstagramAppSettings>('/api/settings/instagram', body),
    email: () => get<EmailSettings>('/api/settings/email'),
    updateEmail: (body: EmailSettingsIn) => put<EmailSettings>('/api/settings/email', body),
    testEmail: () => post<{ sent_to: string }>('/api/settings/email/test'),
  },

  accounts: {
    list: () => get<Account[]>('/api/accounts'),
    oauthStart: () => get<{ url: string }>('/api/instagram/oauth/start'),
    connectToken: (access_token: string) => post<Account>('/api/accounts/token', { access_token }),
    subscribe: (id: number) => post<Account>(`/api/accounts/${id}/subscribe`),
    refreshToken: (id: number) => post<Account>(`/api/accounts/${id}/refresh-token`),
    setActive: (id: number, is_active: boolean) => patch<Account>(`/api/accounts/${id}`, { is_active }),
    remove: (id: number) => del(`/api/accounts/${id}`),
    media: (id: number, after?: string | null) => get<MediaPage>(`/api/accounts/${id}/media`, { after }),
  },

  automations: {
    list: (accountId: number) => get<Automation[]>('/api/automations', { account_id: accountId }),
    get: (id: number) => get<Automation>(`/api/automations/${id}`),
    create: (body: AutomationIn) => post<Automation>('/api/automations', body),
    update: (id: number, body: AutomationIn) => put<Automation>(`/api/automations/${id}`, body),
    setActive: (id: number, is_active: boolean) => patch<Automation>(`/api/automations/${id}/active`, { is_active }),
    duplicate: (id: number) => post<Automation>(`/api/automations/${id}/duplicate`),
    remove: (id: number) => del(`/api/automations/${id}`),
    stats: (id: number) => get<AutomationStats>(`/api/automations/${id}/stats`),
  },

  contacts: {
    list: (q: { account_id: number; q?: string; tag?: string } & PageQuery) => get<Paginated<Contact>>('/api/contacts', q),
    exportUrl: (accountId: number) => withQuery('/api/contacts/export.csv', { account_id: accountId }),
    get: (id: number) => get<Contact>(`/api/contacts/${id}`),
    messages: (id: number) => get<Message[]>(`/api/contacts/${id}/messages`),
    runs: (id: number) => get<Run[]>(`/api/contacts/${id}/runs`),
    remove: (id: number) => del(`/api/contacts/${id}`),
  },

  activity: {
    comments: (q: { account_id: number; status?: string } & PageQuery) =>
      get<Paginated<CommentEvent>>('/api/activity/comments', q),
    runs: (q: { account_id: number; status?: string } & PageQuery) => get<Paginated<Run>>('/api/activity/runs', q),
    webhooks: (limit = 50) => get<WebhookLogEntry[]>('/api/activity/webhooks', { limit }),
    failedJobs: (limit = 50) => get<FailedJob[]>('/api/activity/failed-jobs', { limit }),
  },

  stats: (accountId: number) => get<Stats>('/api/stats', { account_id: accountId }),
}
