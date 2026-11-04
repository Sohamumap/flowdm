// Types mirroring docs/API.md. Timestamps are ISO-8601 strings in UTC.

// ------------------------------------------------------------------ auth

export interface User {
  id: number
  email: string
}

// ------------------------------------------------------------------ setup

export interface AppConfig {
  public_base_url: string
  webhook_url: string
  webhook_verify_token: string
  oauth_redirect_uri: string
  instagram_app_configured: boolean
  graph_api_version: string
}

/** Meta app credentials; secrets are write-only (the API only says whether they're set). */
export interface InstagramAppSettings {
  instagram_app_id: string
  instagram_app_secret_set: boolean
  meta_app_secret_set: boolean
  configured: boolean
}

/** Omit/null = keep, "" = clear (fall back to the server's .env). */
export interface InstagramAppSettingsIn {
  instagram_app_id?: string | null
  instagram_app_secret?: string | null
  meta_app_secret?: string | null
}

export interface EmailSettings {
  alert_email_to: string
  smtp_host: string
  smtp_port: number
  smtp_username: string
  smtp_password_set: boolean
  configured: boolean
}

/** Omit/null = keep, "" = clear. */
export interface EmailSettingsIn {
  alert_email_to?: string | null
  smtp_host?: string | null
  smtp_port?: string | null
  smtp_username?: string | null
  smtp_password?: string | null
}

// ------------------------------------------------------------------ accounts

export interface Account {
  id: number
  ig_user_id: string
  username: string
  name: string | null
  profile_picture_url: string | null
  token_expires_at: string | null
  token_refreshed_at: string | null
  webhooks_subscribed: boolean
  is_active: boolean
  last_error: string | null
  created_at: string
}

export interface Media {
  id: string
  caption: string | null
  media_type: string
  media_product_type: string | null
  media_url: string | null
  thumbnail_url: string | null
  permalink: string | null
  timestamp: string | null
}

export interface MediaPage {
  items: Media[]
  next: string | null
}

// ------------------------------------------------------------------ automations

export type CommentMatch = 'any' | 'contains' | 'exact'
export type DMMatch = 'contains' | 'exact'

export interface CommentTrigger {
  type: 'comment'
  keywords: string[]
  match: CommentMatch
  media_ids: string[]
  public_replies: string[]
  once_per_user: boolean
  top_level_only: boolean
  /** With no media_ids: wait for the next post whose caption has this word, then link to it */
  next_post_word?: string
  /** Set by the server when the wait starts */
  waiting_since?: string | null
}

export interface DMKeywordTrigger {
  type: 'dm_keyword'
  keywords: string[]
  match: DMMatch
}

export type Trigger = CommentTrigger | DMKeywordTrigger
export type TriggerType = Trigger['type']

export type ButtonType = 'step' | 'url'

export interface FlowButton {
  title: string
  type: ButtonType
  step_id?: string | null
  url?: string | null
}

interface StepBase {
  id: string
  label: string
}

export interface MessageStep extends StepBase {
  type: 'message'
  text: string
  buttons: FlowButton[]
  next_step_id: string | null
}

export interface CardStep extends StepBase {
  type: 'card'
  image_url: string
  title: string
  subtitle: string
  buttons: FlowButton[]
  next_step_id: string | null
}

export interface DelayStep extends StepBase {
  type: 'delay'
  seconds: number
  next_step_id: string | null
}

export interface FollowCheckStep extends StepBase {
  type: 'follow_check'
  following_step_id: string | null
  not_following_step_id: string | null
}

export type InputType = 'email' | 'phone' | 'text'

export interface CollectInputStep extends StepBase {
  type: 'collect_input'
  text: string
  input_type: InputType
  field_name: string | null
  retry_text: string
  max_attempts: number
  next_step_id: string | null
}

export interface AddTagStep extends StepBase {
  type: 'add_tag'
  tags: string[]
  next_step_id: string | null
}

export type Step = MessageStep | CardStep | DelayStep | FollowCheckStep | CollectInputStep | AddTagStep
export type StepType = Step['type']

export interface Flow {
  start_step_id: string
  steps: Step[]
}

export interface Automation {
  id: number
  account_id: number
  name: string
  is_active: boolean
  trigger_type: TriggerType
  trigger: Trigger
  flow: Flow
  triggered_count: number
  created_at: string
  updated_at: string
}

export interface AutomationIn {
  account_id: number
  name: string
  is_active: boolean
  trigger: Trigger
  flow: Flow
}

export interface AutomationStats {
  triggered: number
  comments_matched: number
  dms_sent: number
  runs: Record<string, number>
}

// ------------------------------------------------------------------ contacts

export interface Contact {
  id: number
  account_id: number
  igsid: string
  username: string | null
  name: string | null
  profile_pic: string | null
  is_follower: boolean | null
  email: string | null
  phone: string | null
  custom_fields: Record<string, unknown>
  tags: string[]
  last_inbound_at: string | null
  last_interaction_at: string | null
  created_at: string
}

export interface Paginated<T> {
  items: T[]
  total: number
}

export type MessageDirection = 'in' | 'out'

export interface Message {
  id: number
  contact_id: number
  direction: MessageDirection
  /** e.g. text | template | private_reply | postback | quick_reply | attachment */
  kind: string
  text: string | null
  payload: Record<string, unknown> | null
  automation_id: number | null
  error: string | null
  created_at: string
}

export const RUN_STATUSES = [
  'running',
  'waiting_button',
  'waiting_reply',
  'waiting_input',
  'waiting_delay',
  'completed',
  'failed',
  'cancelled',
] as const
export type RunStatus = (typeof RUN_STATUSES)[number]

export interface Run {
  id: number
  automation_id: number
  automation_name: string | null
  contact_id: number
  contact_username: string | null
  status: RunStatus | string
  current_step_id: string | null
  steps_executed: number
  error: string | null
  created_at: string
  updated_at: string
}

// ------------------------------------------------------------------ activity

export const COMMENT_STATUSES = ['no_match', 'matched', 'skipped_repeat', 'ignored', 'error'] as const
export type CommentStatus = (typeof COMMENT_STATUSES)[number]

export interface CommentEvent {
  id: number
  account_id: number
  comment_id: string
  media_id: string | null
  text: string | null
  from_username: string | null
  contact_id: number | null
  automation_id: number | null
  automation_name: string | null
  status: CommentStatus | string
  dm_sent: boolean
  public_reply_id: string | null
  error: string | null
  created_at: string
}

export interface WebhookLogEntry {
  id: number
  received_at: string
  processed_at: string | null
  error: string | null
  payload: unknown
}

export interface FailedJob {
  id: number
  type: string
  attempts: number
  last_error: string | null
  updated_at: string
}

export interface DailyStat {
  date: string
  comments: number
  dms_sent: number
  new_contacts: number
}

export interface Stats {
  contacts: number
  automations_active: number
  comments_24h: number
  comments_matched_24h: number
  dms_sent_24h: number
  messages_in_24h: number
  runs_failed_24h: number
  emails_collected: number
  daily: DailyStat[]
}
