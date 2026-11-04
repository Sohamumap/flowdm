import type {
  Automation,
  AutomationIn,
  CommentMatch,
  DMMatch,
  Flow,
  FlowButton,
  Step,
  StepType,
  Trigger,
  TriggerType,
} from '../types'
import { formatDuration } from './time'
import { charCount, truncate, utf8Bytes } from './utils'

// ------------------------------------------------------------------ limits (docs/API.md)

export const LIMITS = {
  textBytes: 1000,
  textBytesWithButtons: 640,
  buttons: 3,
  buttonTitle: 20,
  cardTitle: 80,
  cardSubtitle: 80,
  delayMin: 1,
  delayMax: 23 * 60 * 60, // 82800
  maxAttemptsMin: 1,
  maxAttemptsMax: 10,
  steps: 50,
  name: 255,
} as const

export const PLACEHOLDERS = ['{first_name}', '{username}'] as const

export const STEP_TYPE_LABELS: Record<StepType, string> = {
  message: 'Message',
  card: 'Card',
  delay: 'Delay',
  follow_check: 'Follow check',
  collect_input: 'Collect input',
  add_tag: 'Add tag',
}

export const STEP_TYPE_DESCRIPTIONS: Record<StepType, string> = {
  message: 'Text with up to 3 buttons',
  card: 'Image, title and buttons',
  delay: 'Wait before the next step',
  follow_check: 'Branch on whether they follow you',
  collect_input: 'Ask for an email, phone or answer',
  add_tag: 'Tag the contact for segmenting',
}

export const STEP_TYPE_ORDER: StepType[] = ['message', 'card', 'collect_input', 'follow_check', 'delay', 'add_tag']

// ------------------------------------------------------------------ ids & defaults

const BASE36 = '0123456789abcdefghijklmnopqrstuvwxyz'

function randomBase36(length: number): string {
  const bytes = new Uint8Array(length)
  crypto.getRandomValues(bytes)
  let out = ''
  for (const b of bytes) out += BASE36[b % 36]
  return out
}

/** New step id: `s_` + 6 random base36 chars, unique among `existing`. */
export function newStepId(existing: Iterable<string> = []): string {
  const taken = new Set(existing)
  for (;;) {
    const id = `s_${randomBase36(6)}`
    if (!taken.has(id)) return id
  }
}

export function createStep(type: StepType, id: string): Step {
  switch (type) {
    case 'message':
      return { id, label: '', type, text: '', buttons: [], next_step_id: null }
    case 'card':
      return { id, label: '', type, image_url: '', title: '', subtitle: '', buttons: [], next_step_id: null }
    case 'delay':
      return { id, label: '', type, seconds: 300, next_step_id: null }
    case 'follow_check':
      return { id, label: '', type, following_step_id: null, not_following_step_id: null }
    case 'collect_input':
      return {
        id,
        label: '',
        type,
        text: "What's your email address?",
        input_type: 'email',
        field_name: null,
        retry_text: "Hmm, that doesn't look right. Please try again.",
        max_attempts: 3,
        next_step_id: null,
      }
    case 'add_tag':
      return { id, label: '', type, tags: [], next_step_id: null }
  }
}

// ------------------------------------------------------------------ labels

/** Short text describing a step's content (text, title, delay...). */
export function stepContent(step: Step): string {
  switch (step.type) {
    case 'message':
    case 'collect_input':
      return step.text
    case 'card':
      return step.title
    case 'delay':
      return step.seconds > 0 ? `wait ${formatDuration(step.seconds)}` : ''
    case 'add_tag':
      return step.tags.join(', ')
    case 'follow_check':
      return ''
  }
}

/** `#<n> <label or type>: <first 30 chars of text/title>` */
export function stepOptionLabel(step: Step, index: number): string {
  const name = step.label.trim() || STEP_TYPE_LABELS[step.type]
  const content = stepContent(step).replace(/\s+/g, ' ').trim()
  return `#${index + 1} ${name}${content ? `: ${truncate(content, 30)}` : ''}`
}

// ------------------------------------------------------------------ references

export function hasButtons(step: Step): step is Extract<Step, { buttons: FlowButton[] }> {
  return step.type === 'message' || step.type === 'card'
}

/** Ids this step can lead to. */
export function stepTargets(step: Step): string[] {
  const out: string[] = []
  if (step.type === 'follow_check') {
    if (step.following_step_id) out.push(step.following_step_id)
    if (step.not_following_step_id) out.push(step.not_following_step_id)
    return out
  }
  if (step.next_step_id) out.push(step.next_step_id)
  if (hasButtons(step)) {
    for (const b of step.buttons) if (b.type === 'step' && b.step_id) out.push(b.step_id)
  }
  return out
}

/** Remove every reference to `deletedId` (next, branches, button targets become null). */
export function clearReferences(step: Step, deletedId: string): Step {
  const fix = (v: string | null | undefined) => (v === deletedId ? null : (v ?? null))
  if (step.type === 'follow_check') {
    return { ...step, following_step_id: fix(step.following_step_id), not_following_step_id: fix(step.not_following_step_id) }
  }
  if (hasButtons(step)) {
    return {
      ...step,
      next_step_id: fix(step.next_step_id),
      buttons: step.buttons.map((b) => (b.type === 'step' && b.step_id === deletedId ? { ...b, step_id: null } : b)),
    }
  }
  return { ...step, next_step_id: fix(step.next_step_id) }
}

export function removeStep(flow: Flow, id: string): Flow {
  const steps = flow.steps.filter((s) => s.id !== id).map((s) => clearReferences(s, id))
  const start = flow.start_step_id === id ? (steps[0]?.id ?? '') : flow.start_step_id
  return { start_step_id: start, steps }
}

/** Ids reachable from the start step. */
export function reachableIds(flow: Flow): Set<string> {
  const byId = new Map(flow.steps.map((s) => [s.id, s]))
  const seen = new Set<string>()
  const queue = [flow.start_step_id]
  while (queue.length) {
    const id = queue.pop()!
    if (seen.has(id)) continue
    const step = byId.get(id)
    if (!step) continue
    seen.add(id)
    queue.push(...stepTargets(step))
  }
  return seen
}

// ------------------------------------------------------------------ trigger summary

function quoteList(keywords: string[]): string {
  return keywords.map((k) => `'${k}'`).join(', ')
}

export function triggerSummary(trigger: Trigger): string {
  if (trigger.type === 'comment') {
    const what =
      trigger.match === 'any'
        ? 'Any comment'
        : `Comment ${trigger.match} ${trigger.keywords.length ? quoteList(trigger.keywords) : '(no keywords)'}`
    const n = trigger.media_ids.length
    const where =
      n === 0 && trigger.next_post_word
        ? `waiting for the next post with '${trigger.next_post_word}'`
        : n === 0
          ? 'all posts'
          : n === 1
            ? '1 post'
            : `${n} posts`
    return `${what} · ${where}`
  }
  return `DM ${trigger.match} ${trigger.keywords.length ? quoteList(trigger.keywords) : '(no keywords)'}`
}

// ------------------------------------------------------------------ editor draft

export interface TriggerDraft {
  type: TriggerType
  keywords: string[]
  commentMatch: CommentMatch
  dmMatch: DMMatch
  postsMode: 'all' | 'specific' | 'next'
  media_ids: string[]
  next_post_word: string
  waiting_since: string | null
  public_replies: string[]
  once_per_user: boolean
  top_level_only: boolean
}

export interface EditorDraft {
  name: string
  is_active: boolean
  trigger: TriggerDraft
  flow: Flow
}

export function defaultTriggerDraft(type: TriggerType = 'comment'): TriggerDraft {
  return {
    type,
    keywords: [],
    commentMatch: 'contains',
    dmMatch: 'contains',
    postsMode: 'all',
    media_ids: [],
    next_post_word: '',
    waiting_since: null,
    public_replies: [],
    once_per_user: true,
    top_level_only: false,
  }
}

export function triggerToDraft(trigger: Trigger): TriggerDraft {
  const base = defaultTriggerDraft(trigger.type)
  if (trigger.type === 'comment') {
    return {
      ...base,
      keywords: [...(trigger.keywords ?? [])],
      commentMatch: trigger.match,
      postsMode: trigger.media_ids?.length ? 'specific' : trigger.next_post_word ? 'next' : 'all',
      media_ids: [...(trigger.media_ids ?? [])],
      next_post_word: trigger.next_post_word ?? '',
      waiting_since: trigger.waiting_since ?? null,
      public_replies: [...(trigger.public_replies ?? [])],
      once_per_user: trigger.once_per_user ?? true,
      top_level_only: trigger.top_level_only ?? false,
    }
  }
  return { ...base, keywords: [...(trigger.keywords ?? [])], dmMatch: trigger.match }
}

export function draftToTrigger(d: TriggerDraft): Trigger {
  const keywords = d.keywords.map((k) => k.trim()).filter(Boolean)
  if (d.type === 'comment') {
    return {
      type: 'comment',
      keywords: d.commentMatch === 'any' ? [] : keywords,
      match: d.commentMatch,
      media_ids: d.postsMode === 'specific' ? [...d.media_ids] : [],
      next_post_word: d.postsMode === 'next' ? d.next_post_word.trim() : '',
      // Sent back unchanged so editing a waiting automation keeps when the wait started
      waiting_since: d.postsMode === 'next' ? d.waiting_since : null,
      public_replies: d.public_replies.map((r) => r.trim()).filter(Boolean),
      once_per_user: d.once_per_user,
      top_level_only: d.top_level_only,
    }
  }
  return { type: 'dm_keyword', keywords, match: d.dmMatch }
}

/** Fill defaults for any fields an older/partial step might be missing. */
export function normalizeStep(raw: Step): Step {
  const base = createStep(raw.type, raw.id)
  const merged = { ...base, ...raw, label: raw.label ?? '' } as Step
  if (hasButtons(merged)) merged.buttons = (merged.buttons ?? []).map((b) => ({ ...b }))
  if (merged.type === 'add_tag') merged.tags = [...(merged.tags ?? [])]
  return merged
}

export function automationToDraft(a: Automation): EditorDraft {
  return {
    name: a.name,
    is_active: a.is_active,
    trigger: triggerToDraft(a.trigger),
    flow: { start_step_id: a.flow.start_step_id, steps: a.flow.steps.map(normalizeStep) },
  }
}

function cleanButton(b: FlowButton): FlowButton {
  return b.type === 'url'
    ? { title: b.title.trim(), type: 'url', url: (b.url ?? '').trim() }
    : { title: b.title.trim(), type: 'step', step_id: b.step_id ?? null }
}

/** Strip editor-only state and irrelevant fields before sending to the API. */
export function cleanStep(step: Step): Step {
  switch (step.type) {
    case 'message':
      return {
        id: step.id,
        label: step.label.trim(),
        type: 'message',
        text: step.text,
        buttons: step.buttons.map(cleanButton),
        next_step_id: step.next_step_id,
      }
    case 'card':
      return {
        id: step.id,
        label: step.label.trim(),
        type: 'card',
        image_url: step.image_url.trim(),
        title: step.title,
        subtitle: step.subtitle,
        buttons: step.buttons.map(cleanButton),
        next_step_id: step.next_step_id,
      }
    case 'delay':
      return { id: step.id, label: step.label.trim(), type: 'delay', seconds: Math.round(step.seconds), next_step_id: step.next_step_id }
    case 'follow_check':
      return {
        id: step.id,
        label: step.label.trim(),
        type: 'follow_check',
        following_step_id: step.following_step_id,
        not_following_step_id: step.not_following_step_id,
      }
    case 'collect_input':
      return {
        id: step.id,
        label: step.label.trim(),
        type: 'collect_input',
        text: step.text,
        input_type: step.input_type,
        field_name: step.input_type === 'text' ? (step.field_name ?? '').trim() || null : null,
        retry_text: step.retry_text,
        max_attempts: step.max_attempts,
        next_step_id: step.next_step_id,
      }
    case 'add_tag':
      return {
        id: step.id,
        label: step.label.trim(),
        type: 'add_tag',
        tags: step.tags.map((t) => t.trim()).filter(Boolean),
        next_step_id: step.next_step_id,
      }
  }
}

export function draftToPayload(d: EditorDraft, accountId: number): AutomationIn {
  return {
    account_id: accountId,
    name: d.name.trim(),
    is_active: d.is_active,
    trigger: draftToTrigger(d.trigger),
    flow: { start_step_id: d.flow.start_step_id, steps: d.flow.steps.map(cleanStep) },
  }
}

// ------------------------------------------------------------------ validation

export type IssueLevel = 'error' | 'warning'

export interface Issue {
  level: IssueLevel
  message: string
  /** Step the issue belongs to (shown inline on that step card). */
  stepId?: string
  /** Section for non-step issues. */
  section?: 'name' | 'trigger' | 'flow'
  /** Field within the trigger section. */
  field?: 'keywords' | 'posts'
  /** Show inline even before the first save attempt (design guidance, not a typo). */
  always?: boolean
}

export const BUTTON_WARNING =
  'Instagram only lets you send more messages after the person taps a button or replies to this first DM. Add a button to continue the flow reliably.'

function stepName(step: Step, index: number): string {
  return `Step #${index + 1}${step.label.trim() ? ` (${step.label.trim()})` : ''}`
}

function validateButtons(step: Extract<Step, { buttons: FlowButton[] }>, index: number, ids: Set<string>, issues: Issue[]) {
  const name = stepName(step, index)
  if (step.buttons.length > LIMITS.buttons) {
    issues.push({ level: 'error', stepId: step.id, message: `${name}: at most ${LIMITS.buttons} buttons` })
  }
  step.buttons.forEach((b, i) => {
    const label = `${name}, button ${i + 1}`
    const len = charCount(b.title.trim())
    if (len === 0) issues.push({ level: 'error', stepId: step.id, message: `${label}: add a title` })
    else if (len > LIMITS.buttonTitle)
      issues.push({ level: 'error', stepId: step.id, message: `${label}: title is longer than ${LIMITS.buttonTitle} characters` })
    if (b.type === 'step') {
      if (!b.step_id) issues.push({ level: 'error', stepId: step.id, message: `${label}: choose the step it goes to` })
      else if (!ids.has(b.step_id)) issues.push({ level: 'error', stepId: step.id, message: `${label}: points to a deleted step` })
    } else if (!/^https?:\/\/\S+$/i.test((b.url ?? '').trim())) {
      issues.push({ level: 'error', stepId: step.id, message: `${label}: enter a link starting with https://` })
    }
  })
}

export function validateDraft(d: EditorDraft): Issue[] {
  const issues: Issue[] = []
  const { trigger, flow } = d

  // name
  if (!d.name.trim()) issues.push({ level: 'error', section: 'name', message: 'Give the automation a name' })
  else if (d.name.trim().length > LIMITS.name)
    issues.push({ level: 'error', section: 'name', message: `Name is longer than ${LIMITS.name} characters` })

  // trigger
  const keywords = trigger.keywords.filter((k) => k.trim())
  if (trigger.type === 'comment') {
    if (trigger.commentMatch !== 'any' && keywords.length === 0)
      issues.push({ level: 'error', section: 'trigger', field: 'keywords', message: "Add at least one keyword, or choose 'Any comment'" })
    if (trigger.postsMode === 'specific' && trigger.media_ids.length === 0)
      issues.push({ level: 'error', section: 'trigger', field: 'posts', message: "Select at least one post, or choose 'All posts & reels'" })
    if (trigger.postsMode === 'next' && !trigger.next_post_word.trim())
      issues.push({ level: 'error', section: 'trigger', field: 'posts', message: 'Type the word your next post’s caption will contain' })
  } else if (keywords.length === 0) {
    issues.push({ level: 'error', section: 'trigger', field: 'keywords', message: 'Add at least one keyword' })
  }

  // flow structure
  if (flow.steps.length === 0) {
    issues.push({ level: 'error', section: 'flow', message: 'Add at least one step', always: true })
    return issues
  }
  if (flow.steps.length > LIMITS.steps)
    issues.push({ level: 'error', section: 'flow', message: `A flow can have at most ${LIMITS.steps} steps`, always: true })

  const ids = new Set(flow.steps.map((s) => s.id))
  const start = flow.steps.find((s) => s.id === flow.start_step_id)
  if (!start) issues.push({ level: 'error', section: 'flow', message: 'Choose a start step', always: true })

  if (trigger.type === 'comment' && start) {
    if (start.type !== 'message' && start.type !== 'card') {
      issues.push({
        level: 'error',
        stepId: start.id,
        always: true,
        message:
          'Comment automations must start with a Message or Card step (it is sent as the private reply to the comment).',
      })
    } else if (start.next_step_id && !start.buttons.some((b) => b.type === 'step')) {
      issues.push({ level: 'warning', stepId: start.id, always: true, message: BUTTON_WARNING })
    }
  }

  const reachable = reachableIds(flow)

  flow.steps.forEach((step, index) => {
    const name = stepName(step, index)
    const ref = (id: string | null | undefined, what: string) => {
      if (id && !ids.has(id)) issues.push({ level: 'error', stepId: step.id, message: `${name}: ${what} points to a deleted step` })
    }
    switch (step.type) {
      case 'message': {
        const limit = step.buttons.length ? LIMITS.textBytesWithButtons : LIMITS.textBytes
        if (!step.text.trim()) issues.push({ level: 'error', stepId: step.id, message: `${name}: write a message` })
        else if (utf8Bytes(step.text) > limit)
          issues.push({ level: 'error', stepId: step.id, message: `${name}: message is too long (max ${limit} bytes)` })
        validateButtons(step, index, ids, issues)
        ref(step.next_step_id, '“Then”')
        break
      }
      case 'card': {
        if (!step.image_url.trim().startsWith('https://'))
          issues.push({ level: 'error', stepId: step.id, message: `${name}: image URL must start with https://` })
        const t = charCount(step.title)
        if (!step.title.trim()) issues.push({ level: 'error', stepId: step.id, message: `${name}: add a title` })
        else if (t > LIMITS.cardTitle)
          issues.push({ level: 'error', stepId: step.id, message: `${name}: title is longer than ${LIMITS.cardTitle} characters` })
        if (charCount(step.subtitle) > LIMITS.cardSubtitle)
          issues.push({ level: 'error', stepId: step.id, message: `${name}: subtitle is longer than ${LIMITS.cardSubtitle} characters` })
        validateButtons(step, index, ids, issues)
        ref(step.next_step_id, '“Then”')
        break
      }
      case 'delay':
        if (!Number.isInteger(step.seconds) || step.seconds < LIMITS.delayMin || step.seconds > LIMITS.delayMax)
          issues.push({ level: 'error', stepId: step.id, message: `${name}: delay must be between 1 second and 23 hours` })
        ref(step.next_step_id, '“Then”')
        break
      case 'follow_check':
        ref(step.following_step_id, '“If following”')
        ref(step.not_following_step_id, '“If not following”')
        if (!step.following_step_id && !step.not_following_step_id)
          issues.push({ level: 'warning', stepId: step.id, message: `${name}: both branches end the flow` })
        break
      case 'collect_input':
        if (!step.text.trim()) issues.push({ level: 'error', stepId: step.id, message: `${name}: write the question` })
        else if (utf8Bytes(step.text) > LIMITS.textBytes)
          issues.push({ level: 'error', stepId: step.id, message: `${name}: question is too long (max ${LIMITS.textBytes} bytes)` })
        if (step.input_type === 'text' && !(step.field_name ?? '').trim())
          issues.push({ level: 'error', stepId: step.id, message: `${name}: give the answer a field name to save it under` })
        if (!step.retry_text.trim())
          issues.push({ level: 'error', stepId: step.id, message: `${name}: write a retry message for invalid answers` })
        else if (utf8Bytes(step.retry_text) > LIMITS.textBytes)
          issues.push({ level: 'error', stepId: step.id, message: `${name}: retry message is too long` })
        if (!Number.isInteger(step.max_attempts) || step.max_attempts < LIMITS.maxAttemptsMin || step.max_attempts > LIMITS.maxAttemptsMax)
          issues.push({ level: 'error', stepId: step.id, message: `${name}: max attempts must be between 1 and 10` })
        ref(step.next_step_id, '“Then”')
        break
      case 'add_tag':
        if (!step.tags.some((t) => t.trim())) issues.push({ level: 'error', stepId: step.id, message: `${name}: add at least one tag` })
        ref(step.next_step_id, '“Then”')
        break
    }
    if (start && !reachable.has(step.id)) {
      issues.push({
        level: 'warning',
        stepId: step.id,
        always: true,
        message: 'Nothing leads to this step yet. Link it from a button, a “Then” or a branch.',
      })
    }
  })

  return issues
}
