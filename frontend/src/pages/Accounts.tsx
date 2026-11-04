import {
  CircleAlert,
  Clock,
  ExternalLink,
  KeyRound,
  Lock,
  Mail,
  Power,
  RefreshCw,
  Trash2,
  Webhook,
} from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, errorMessage } from '../api'
import { Avatar } from '../components/Avatar'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { Card, CardBody, CardHeader } from '../components/Card'
import { CopyField } from '../components/CopyField'
import { EmptyState } from '../components/EmptyState'
import { Field } from '../components/Field'
import { InstagramIcon } from '../components/InstagramIcon'
import { useConfirm } from '../components/Modal'
import { ErrorNotice, Notice } from '../components/Notice'
import { PageHeader } from '../components/PageHeader'
import { LoadingBlock } from '../components/Spinner'
import { useToast } from '../components/Toast'
import { useAccounts } from '../lib/accounts'
import { useAsync, useNow } from '../lib/hooks'
import { daysUntil, formatDateTime, timeAgo } from '../lib/time'
import { cn } from '../lib/utils'
import type { Account, AppConfig } from '../types'

export default function AccountsPage() {
  const [params, setParams] = useSearchParams()
  const { reload } = useAccounts()
  const config = useAsync(() => api.config(), [])
  const connected = params.get('connected')
  const oauthError = params.get('error')

  useEffect(() => {
    if (connected) void reload()
  }, [connected, reload])

  const clearParams = () => setParams({}, { replace: true })

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title="Settings" description="Instagram accounts, webhook setup and your login." />

      <div className="space-y-6">
        {connected && (
          <Notice tone="success" title="Instagram account connected" onDismiss={clearParams}>
            Make sure webhooks are subscribed below so FlowDM receives comments and messages.
          </Notice>
        )}
        {oauthError && (
          <Notice tone="error" title="Couldn't connect Instagram" onDismiss={clearParams}>
            {oauthError}
          </Notice>
        )}

        <AccountList />
        <WebhookSetup config={config.data} error={config.error} loading={config.loading} onRetry={() => void config.reload()} />
        <InstagramAppCard onSaved={() => void config.reload()} />
        <EmailAlertsCard />
        <ConnectCard config={config.data} configLoading={config.loading} />
        <ChangePassword />
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ accounts

function TokenExpiry({ account, now }: { account: Account; now: Date }) {
  const days = daysUntil(account.token_expires_at, now)
  if (days === null) {
    return (
      <span className="inline-flex items-center gap-1 text-slate-500">
        <Clock className="size-3.5" />
        Token expiry unknown
      </span>
    )
  }
  const expired = days <= 0
  const warn = !expired && days < 10
  return (
    <span
      title={formatDateTime(account.token_expires_at)}
      className={cn(
        'inline-flex items-center gap-1',
        expired ? 'font-medium text-red-600' : warn ? 'font-medium text-amber-700' : 'text-slate-500',
      )}
    >
      {expired || warn ? <CircleAlert className="size-3.5" /> : <Clock className="size-3.5" />}
      {expired ? `Token expired ${timeAgo(account.token_expires_at, now)}` : `Token expires ${timeAgo(account.token_expires_at, now)}`}
      {warn && ' · refresh it soon'}
    </span>
  )
}

function AccountList() {
  const { accounts, loading, error, reload, upsert, account: selected, select } = useAccounts()
  const toast = useToast()
  const confirm = useConfirm()
  const now = useNow(60_000)
  const [busy, setBusy] = useState<string | null>(null)

  const run = async (key: string, fn: () => Promise<Account | void>, success: string) => {
    setBusy(key)
    try {
      const result = await fn()
      if (result) upsert(result)
      toast.success(success)
    } catch (err) {
      toast.error(errorMessage(err))
      void reload()
    } finally {
      setBusy(null)
    }
  }

  const remove = async (a: Account) => {
    const ok = await confirm({
      title: `Delete @${a.username}?`,
      message: (
        <>
          This disconnects the account and permanently deletes <strong>its automations, contacts and logs</strong>. This can't be
          undone.
        </>
      ),
      confirmLabel: 'Delete account',
      danger: true,
    })
    if (!ok) return
    setBusy(`${a.id}:delete`)
    try {
      await api.accounts.remove(a.id)
      toast.success(`@${a.username} deleted`)
      await reload()
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card>
      <CardHeader
        icon={<InstagramIcon className="size-4" />}
        title="Instagram accounts"
        description="Professional (business or creator) accounts FlowDM replies for."
      />
      {loading ? (
        <LoadingBlock />
      ) : error && !accounts.length ? (
        <CardBody>
          <ErrorNotice error={error} onRetry={() => void reload()} />
        </CardBody>
      ) : accounts.length === 0 ? (
        <EmptyState
          icon={<InstagramIcon className="size-6" />}
          title="No accounts connected yet"
          description="Connect an Instagram professional account below to start automating comments and DMs."
        />
      ) : (
        <ul className="divide-y divide-slate-100">
          {accounts.map((a) => {
            const isBusy = (action: string) => busy === `${a.id}:${action}`
            return (
              <li key={a.id} className="px-4 py-4 sm:px-5">
                <div className="flex flex-col gap-4 md:flex-row md:items-start">
                  <div className="flex min-w-0 flex-1 items-start gap-3">
                    <Avatar src={a.profile_picture_url} name={a.name} username={a.username} size={44} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <a
                          href={`https://instagram.com/${encodeURIComponent(a.username)}`}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 font-semibold text-slate-900 hover:text-indigo-700"
                        >
                          @{a.username}
                          <ExternalLink className="size-3.5 text-slate-400" />
                        </a>
                        {a.id === selected?.id && accounts.length > 1 && <Badge tone="indigo">Selected</Badge>}
                        <Badge tone={a.is_active ? 'green' : 'gray'} dot>
                          {a.is_active ? 'Active' : 'Paused'}
                        </Badge>
                        <Badge tone={a.webhooks_subscribed ? 'green' : 'amber'} icon={<Webhook className="size-3" />}>
                          {a.webhooks_subscribed ? 'Webhooks subscribed' : 'Webhooks not subscribed'}
                        </Badge>
                      </div>
                      {a.name && <p className="text-sm text-slate-500">{a.name}</p>}
                      <p className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs">
                        <TokenExpiry account={a} now={now} />
                        {a.token_refreshed_at && (
                          <span className="text-slate-400">Refreshed {timeAgo(a.token_refreshed_at, now)}</span>
                        )}
                        <span className="text-slate-400">Connected {timeAgo(a.created_at, now)}</span>
                      </p>
                      {a.last_error && (
                        <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-red-50 px-2.5 py-1.5 text-xs text-red-700">
                          <CircleAlert className="mt-px size-3.5 shrink-0" />
                          <span className="break-words">{a.last_error}</span>
                        </p>
                      )}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2 md:max-w-[19rem] md:justify-end">
                    {a.id !== selected?.id && (
                      <Button size="sm" variant="ghost" onClick={() => select(a.id)}>
                        Select
                      </Button>
                    )}
                    <Button
                      size="sm"
                      icon={<Webhook className="size-4" />}
                      loading={isBusy('subscribe')}
                      disabled={!!busy}
                      onClick={() => void run(`${a.id}:subscribe`, () => api.accounts.subscribe(a.id), 'Webhooks subscribed')}
                    >
                      {a.webhooks_subscribed ? 'Resubscribe' : 'Subscribe webhooks'}
                    </Button>
                    <Button
                      size="sm"
                      icon={<RefreshCw className="size-4" />}
                      loading={isBusy('refresh')}
                      disabled={!!busy}
                      onClick={() => void run(`${a.id}:refresh`, () => api.accounts.refreshToken(a.id), 'Token refreshed')}
                    >
                      Refresh token
                    </Button>
                    <Button
                      size="sm"
                      icon={<Power className="size-4" />}
                      loading={isBusy('active')}
                      disabled={!!busy}
                      onClick={() =>
                        void run(
                          `${a.id}:active`,
                          () => api.accounts.setActive(a.id, !a.is_active),
                          a.is_active ? `@${a.username} paused` : `@${a.username} activated`,
                        )
                      }
                    >
                      {a.is_active ? 'Deactivate' : 'Activate'}
                    </Button>
                    <Button
                      size="sm"
                      variant="danger-ghost"
                      icon={<Trash2 className="size-4" />}
                      loading={isBusy('delete')}
                      disabled={!!busy}
                      onClick={() => void remove(a)}
                    >
                      Delete
                    </Button>
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}

// ------------------------------------------------------------------ connect

function ConnectCard({ config, configLoading }: { config?: AppConfig; configLoading: boolean }) {
  const { upsert, select } = useAccounts()
  const toast = useToast()
  const [redirecting, setRedirecting] = useState(false)
  const [token, setToken] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [tokenError, setTokenError] = useState<string | null>(null)
  const appConfigured = !!config?.instagram_app_configured

  const startOAuth = async () => {
    setRedirecting(true)
    try {
      const { url } = await api.accounts.oauthStart()
      window.location.assign(url)
    } catch (err) {
      toast.error(errorMessage(err))
      setRedirecting(false)
    }
  }

  const submitToken = async (e: FormEvent) => {
    e.preventDefault()
    const value = token.trim()
    if (!value) return
    setSubmitting(true)
    setTokenError(null)
    try {
      const account = await api.accounts.connectToken(value)
      upsert(account)
      select(account.id)
      setToken('')
      toast.success(`Connected @${account.username}`)
    } catch (err) {
      setTokenError(errorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card>
      <CardHeader icon={<KeyRound className="size-4" />} title="Connect an account" description="Use Instagram login, or paste a token." />
      <CardBody className="grid gap-6 md:grid-cols-2 md:gap-0 md:divide-x md:divide-slate-100">
        <div className="md:pr-6">
          <h3 className="text-sm font-semibold text-slate-900">Connect with Instagram</h3>
          <p className="mt-1 text-sm text-slate-500">
            You'll be sent to Instagram to approve access, then brought back here.
          </p>
          <button
            type="button"
            onClick={() => void startOAuth()}
            disabled={!appConfigured || redirecting}
            className="mt-4 inline-flex h-10 items-center gap-2 rounded-lg bg-gradient-to-r from-fuchsia-600 via-rose-500 to-amber-500 px-4 text-sm font-semibold text-white shadow-sm transition hover:brightness-105 disabled:cursor-not-allowed disabled:from-slate-300 disabled:via-slate-300 disabled:to-slate-300 disabled:shadow-none"
          >
            <InstagramIcon className="size-4" />
            {redirecting ? 'Redirecting…' : 'Connect with Instagram'}
          </button>
          {!configLoading && !appConfigured && (
            <p className="mt-3 flex items-start gap-1.5 text-xs text-amber-700">
              <CircleAlert className="mt-px size-3.5 shrink-0" />
              <span>
                Add your Instagram app ID and secret in the “Instagram app” card above first, or paste an access token
                instead.
              </span>
            </p>
          )}
        </div>
        <form onSubmit={submitToken} className="border-t border-slate-100 pt-6 md:border-t-0 md:pt-0 md:pl-6">
          <h3 className="text-sm font-semibold text-slate-900">Paste access token</h3>
          <p className="mt-1 text-sm text-slate-500">
            Generate it in Meta App Dashboard → Instagram → API setup → Generate token.
          </p>
          <div className="mt-4 flex flex-col gap-2 sm:flex-row">
            <input
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="IGAA…"
              aria-label="Instagram access token"
              autoComplete="off"
              spellCheck={false}
              className={cn('input min-w-0 flex-1 font-mono text-[13px]', tokenError && 'input-error')}
            />
            <Button type="submit" variant="primary" loading={submitting} disabled={!token.trim()}>
              Connect
            </Button>
          </div>
          {tokenError && <p className="mt-2 text-xs font-medium text-red-600">{tokenError}</p>}
        </form>
      </CardBody>
    </Card>
  )
}

// ------------------------------------------------------------------ webhook setup

function WebhookSetup({
  config,
  error,
  loading,
  onRetry,
}: {
  config?: AppConfig
  error: string | null
  loading: boolean
  onRetry: () => void
}) {
  return (
    <Card>
      <CardHeader
        icon={<Webhook className="size-4" />}
        title="Webhook setup"
        description="Paste these into Meta App Dashboard → Instagram → API setup with Instagram login"
        actions={config && <Badge tone="gray">Graph API {config.graph_api_version}</Badge>}
      />
      <CardBody className="space-y-4">
        {loading ? (
          <LoadingBlock className="py-6" />
        ) : error || !config ? (
          <ErrorNotice error={error ?? 'Unavailable'} onRetry={onRetry} />
        ) : (
          <>
            <CopyField label="Webhook callback URL" value={config.webhook_url} hint="Subscribe to the comments, messages and messaging_postbacks fields." />
            <CopyField label="Webhook verify token" value={config.webhook_verify_token} hint="Paste it into the “Verify token” box next to the callback URL." />
            <CopyField label="OAuth redirect URI" value={config.oauth_redirect_uri} hint="Add it under Business login settings → OAuth redirect URIs." />
            <div className="flex items-center gap-2 text-xs">
              {config.instagram_app_configured ? (
                <Badge tone="green" dot>
                  Instagram app configured
                </Badge>
              ) : (
                <Badge tone="amber" dot>
                  Instagram app ID and secret not added yet
                </Badge>
              )}
              <span className="truncate text-slate-400">{config.public_base_url}</span>
            </div>
          </>
        )}
      </CardBody>
    </Card>
  )
}

// ------------------------------------------------------------------ instagram app keys

function InstagramAppCard({ onSaved }: { onSaved: () => void }) {
  const toast = useToast()
  const current = useAsync(() => api.settings.instagramApp(), [])
  const [appId, setAppId] = useState<string | null>(null)
  const [appSecret, setAppSecret] = useState('')
  const [metaSecret, setMetaSecret] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const data = current.data
  const appIdValue = appId ?? data?.instagram_app_id ?? ''
  const dirty = (appId !== null && appId !== data?.instagram_app_id) || appSecret !== '' || metaSecret !== ''

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setSaving(true)
    try {
      await api.settings.updateInstagramApp({
        instagram_app_id: appId === null ? null : appId.trim(),
        instagram_app_secret: appSecret.trim() || null,
        meta_app_secret: metaSecret.trim() || null,
      })
      setAppId(null)
      setAppSecret('')
      setMetaSecret('')
      await current.reload()
      onSaved()
      toast.success('Instagram app saved')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const secretHint = (isSet: boolean | undefined) =>
    isSet ? 'Saved. Leave empty to keep it, or paste a new one to replace it.' : undefined

  return (
    <Card>
      <CardHeader
        icon={<KeyRound className="size-4" />}
        title="Instagram app"
        description="From Meta App Dashboard → Instagram → API setup with Instagram login."
        actions={
          data &&
          (data.configured ? (
            <Badge tone="green" dot>
              Configured
            </Badge>
          ) : (
            <Badge tone="amber" dot>
              Not configured
            </Badge>
          ))
        }
      />
      <CardBody>
        {current.loading ? (
          <LoadingBlock className="py-6" />
        ) : current.error ? (
          <ErrorNotice error={current.error} onRetry={() => void current.reload()} />
        ) : (
          <form onSubmit={submit} className="grid max-w-xl gap-4">
            {error && <Notice tone="error">{error}</Notice>}
            <Field label="Instagram app ID" htmlFor="ig-app-id" hint="Digits only, e.g. 1234567890123456">
              <input
                id="ig-app-id"
                inputMode="numeric"
                autoComplete="off"
                value={appIdValue}
                onChange={(e) => setAppId(e.target.value)}
                className="input font-mono"
              />
            </Field>
            <Field label="Instagram app secret" htmlFor="ig-app-secret" hint={secretHint(data?.instagram_app_secret_set)}>
              <input
                id="ig-app-secret"
                type="password"
                autoComplete="new-password"
                placeholder={data?.instagram_app_secret_set ? '••••••••••••' : ''}
                value={appSecret}
                onChange={(e) => setAppSecret(e.target.value)}
                className="input font-mono"
              />
            </Field>
            <Field
              label="Meta app secret"
              htmlFor="meta-app-secret"
              optional
              hint={secretHint(data?.meta_app_secret_set) ?? 'App settings → Basic → App secret. Helps verify webhooks.'}
            >
              <input
                id="meta-app-secret"
                type="password"
                autoComplete="new-password"
                placeholder={data?.meta_app_secret_set ? '••••••••••••' : ''}
                value={metaSecret}
                onChange={(e) => setMetaSecret(e.target.value)}
                className="input font-mono"
              />
            </Field>
            <div>
              <Button type="submit" variant="primary" loading={saving} disabled={!dirty}>
                Save
              </Button>
            </div>
          </form>
        )}
      </CardBody>
    </Card>
  )
}

// ------------------------------------------------------------------ email alerts

function EmailAlertsCard() {
  const toast = useToast()
  const current = useAsync(() => api.settings.email(), [])
  const [sender, setSender] = useState<string | null>(null)
  const [password, setPassword] = useState('')
  const [to, setTo] = useState<string | null>(null)
  const [host, setHost] = useState<string | null>(null)
  const [port, setPort] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)

  const data = current.data
  const senderValue = sender ?? data?.smtp_username ?? ''
  const toValue = to ?? (data && data.alert_email_to !== data.smtp_username ? data.alert_email_to : '')
  const hostValue = host ?? data?.smtp_host ?? ''
  const portValue = port ?? (data ? String(data.smtp_port) : '')
  const dirty = sender !== null || to !== null || host !== null || port !== null || password !== ''

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setSaving(true)
    try {
      await api.settings.updateEmail({
        smtp_username: sender === null ? null : sender.trim(),
        smtp_password: password.trim() || null,
        alert_email_to: to === null ? null : to.trim(),
        smtp_host: host === null ? null : host.trim(),
        smtp_port: port === null ? null : port.trim(),
      })
      setSender(null)
      setPassword('')
      setTo(null)
      setHost(null)
      setPort(null)
      await current.reload()
      toast.success('Email alerts saved')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const sendTest = async () => {
    setError(null)
    setTesting(true)
    try {
      const result = await api.settings.testEmail()
      toast.success(`Test email sent to ${result.sent_to}`)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setTesting(false)
    }
  }

  return (
    <Card>
      <CardHeader
        icon={<Mail className="size-4" />}
        title="Email alerts"
        description="FlowDM emails you when a DM fails, a new post goes live without its automation, or Instagram access can't be renewed."
        actions={
          data &&
          (data.configured ? (
            <Badge tone="green" dot>
              On
            </Badge>
          ) : (
            <Badge tone="amber" dot>
              Off
            </Badge>
          ))
        }
      />
      <CardBody>
        {current.loading ? (
          <LoadingBlock className="py-6" />
        ) : current.error ? (
          <ErrorNotice error={current.error} onRetry={() => void current.reload()} />
        ) : (
          <form onSubmit={submit} className="grid max-w-xl gap-4">
            {error && <Notice tone="error">{error}</Notice>}
            <Field label="Gmail address that sends the alerts" htmlFor="smtp-user">
              <input
                id="smtp-user"
                type="email"
                autoComplete="off"
                value={senderValue}
                onChange={(e) => setSender(e.target.value)}
                className="input"
              />
            </Field>
            <Field
              label="App password"
              htmlFor="smtp-password"
              hint={
                data?.smtp_password_set
                  ? 'Saved. Leave empty to keep it, or paste a new one to replace it.'
                  : 'Google Account → Security → 2-Step Verification → App passwords. Create one called FlowDM and paste the 16 letters. Your normal Gmail password won’t work here.'
              }
            >
              <input
                id="smtp-password"
                type="password"
                autoComplete="new-password"
                placeholder={data?.smtp_password_set ? '••••••••••••' : ''}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="input font-mono"
              />
            </Field>
            <Field label="Send alerts to" htmlFor="alert-to" optional hint="Leave empty to send them to the address above.">
              <input
                id="alert-to"
                type="email"
                autoComplete="off"
                value={toValue}
                onChange={(e) => setTo(e.target.value)}
                className="input"
              />
            </Field>
            <details className="text-sm text-slate-600">
              <summary className="cursor-pointer select-none">Not using Gmail?</summary>
              <div className="mt-3 grid gap-4 sm:grid-cols-[1fr_8rem]">
                <Field label="SMTP server" htmlFor="smtp-host">
                  <input id="smtp-host" value={hostValue} onChange={(e) => setHost(e.target.value)} className="input font-mono" />
                </Field>
                <Field label="Port" htmlFor="smtp-port">
                  <input
                    id="smtp-port"
                    inputMode="numeric"
                    value={portValue}
                    onChange={(e) => setPort(e.target.value)}
                    className="input font-mono"
                  />
                </Field>
              </div>
            </details>
            <div className="flex flex-wrap gap-2">
              <Button type="submit" variant="primary" loading={saving} disabled={!dirty}>
                Save
              </Button>
              <Button type="button" loading={testing} disabled={!data?.configured || dirty} onClick={() => void sendTest()}>
                Send test email
              </Button>
            </div>
          </form>
        )}
      </CardBody>
    </Card>
  )
}

// ------------------------------------------------------------------ password

function ChangePassword() {
  const toast = useToast()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const mismatch = repeat.length > 0 && next !== repeat
  const tooShort = next.length > 0 && next.length < 8

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (next !== repeat) return setError('The new passwords don’t match.')
    if (next.length < 8) return setError('Use at least 8 characters.')
    setSaving(true)
    try {
      await api.auth.changePassword(current, next)
      setCurrent('')
      setNext('')
      setRepeat('')
      toast.success('Password changed')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader icon={<Lock className="size-4" />} title="Change password" />
      <CardBody>
        <form onSubmit={submit} className="max-w-md space-y-4">
          {error && <Notice tone="error">{error}</Notice>}
          <Field label="Current password" htmlFor="pw-current">
            <input
              id="pw-current"
              type="password"
              autoComplete="current-password"
              required
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              className="input"
            />
          </Field>
          <Field label="New password" htmlFor="pw-new" error={tooShort ? 'Use at least 8 characters' : undefined}>
            <input
              id="pw-new"
              type="password"
              autoComplete="new-password"
              required
              value={next}
              onChange={(e) => setNext(e.target.value)}
              className={cn('input', tooShort && 'input-error')}
            />
          </Field>
          <Field label="Repeat new password" htmlFor="pw-repeat" error={mismatch ? 'Passwords don’t match' : undefined}>
            <input
              id="pw-repeat"
              type="password"
              autoComplete="new-password"
              required
              value={repeat}
              onChange={(e) => setRepeat(e.target.value)}
              className={cn('input', mismatch && 'input-error')}
            />
          </Field>
          <Button type="submit" variant="primary" loading={saving} disabled={!current || !next || !repeat}>
            Update password
          </Button>
        </form>
      </CardBody>
    </Card>
  )
}
