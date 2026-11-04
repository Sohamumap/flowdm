import { MessageCircle, Plus, Send, Trash2, Zap } from 'lucide-react'
import { useId } from 'react'
import { Button, IconButton } from '../../components/Button'
import { Card, CardBody, CardHeader } from '../../components/Card'
import { ChipInput } from '../../components/ChipInput'
import { Field } from '../../components/Field'
import { Segmented } from '../../components/Tabs'
import type { Issue, TriggerDraft } from '../../lib/flow'
import { cn } from '../../lib/utils'
import type { CommentMatch, DMMatch, TriggerType } from '../../types'
import { MediaPicker } from './MediaPicker'

interface Props {
  trigger: TriggerDraft
  onChange: (patch: Partial<TriggerDraft>) => void
  accountId: number
  issues: Issue[]
  showErrors: boolean
}

const TYPE_OPTIONS: { value: TriggerType; title: string; description: string; icon: typeof MessageCircle }[] = [
  { value: 'comment', title: 'Comment on a post or reel', description: 'Reply in DMs when someone comments', icon: MessageCircle },
  { value: 'dm_keyword', title: 'DM keyword', description: 'Reply when a DM contains a keyword', icon: Send },
]

function Checkbox({
  checked,
  onChange,
  label,
  description,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: string
  description?: string
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3 rounded-lg p-1 text-sm">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 size-4 shrink-0 rounded border-slate-300 accent-indigo-600"
      />
      <span>
        <span className="font-medium text-slate-800">{label}</span>
        {description && <span className="block text-slate-500">{description}</span>}
      </span>
    </label>
  )
}

export function TriggerCard({ trigger, onChange, accountId, issues, showErrors }: Props) {
  const id = useId()
  const keywordError = showErrors ? issues.find((i) => i.field === 'keywords')?.message : undefined
  const postsError = showErrors ? issues.find((i) => i.field === 'posts')?.message : undefined
  const isComment = trigger.type === 'comment'
  const needsKeywords = !isComment || trigger.commentMatch !== 'any'

  return (
    <Card>
      <CardHeader
        icon={<Zap className="size-4" />}
        title="When…"
        description="What starts this automation"
      />
      <CardBody className="space-y-6">
        {/* type */}
        <div role="radiogroup" aria-label="Trigger type" className="grid gap-3 sm:grid-cols-2">
          {TYPE_OPTIONS.map((o) => {
            const active = trigger.type === o.value
            return (
              <button
                key={o.value}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => onChange({ type: o.value })}
                className={cn(
                  'flex items-start gap-3 rounded-xl border p-3.5 text-left transition',
                  active
                    ? 'border-indigo-400 bg-indigo-50/60 ring-1 ring-indigo-400'
                    : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50',
                )}
              >
                <span
                  className={cn(
                    'flex size-9 shrink-0 items-center justify-center rounded-lg',
                    active ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-slate-500',
                  )}
                >
                  <o.icon className="size-[18px]" />
                </span>
                <span className="min-w-0">
                  <span className={cn('block text-sm font-semibold', active ? 'text-indigo-900' : 'text-slate-800')}>{o.title}</span>
                  <span className="block text-xs text-slate-500">{o.description}</span>
                </span>
              </button>
            )
          })}
        </div>

        {/* match + keywords */}
        <div className="space-y-4">
          <Field label={isComment ? 'Which comments?' : 'Which messages?'}>
            {isComment ? (
              <Segmented<CommentMatch>
                ariaLabel="Comment match mode"
                value={trigger.commentMatch}
                onChange={(commentMatch) => onChange({ commentMatch })}
                options={[
                  { value: 'contains', label: 'Contains' },
                  { value: 'exact', label: 'Exact match' },
                  { value: 'any', label: 'Any comment' },
                ]}
              />
            ) : (
              <Segmented<DMMatch>
                ariaLabel="DM match mode"
                value={trigger.dmMatch}
                onChange={(dmMatch) => onChange({ dmMatch })}
                options={[
                  { value: 'contains', label: 'Contains' },
                  { value: 'exact', label: 'Exact match' },
                ]}
              />
            )}
          </Field>
          {needsKeywords && (
            <Field
              label="Keywords"
              htmlFor={`${id}-kw`}
              error={keywordError}
              hint={
                (isComment ? trigger.commentMatch : trigger.dmMatch) === 'exact'
                  ? 'The whole message must equal one keyword. Press Enter or comma to add. Case doesn’t matter.'
                  : 'Fires when a keyword appears as a word in the text. Press Enter or comma to add. Case doesn’t matter.'
              }
            >
              <ChipInput
                id={`${id}-kw`}
                value={trigger.keywords}
                onChange={(keywords) => onChange({ keywords })}
                placeholder={isComment ? 'e.g. LINK, GUIDE' : 'e.g. PRICE'}
                invalid={!!keywordError}
              />
            </Field>
          )}
        </div>

        {isComment && (
          <>
            {/* posts */}
            <fieldset className="space-y-3">
              <legend className="mb-1.5 text-sm font-medium text-slate-700">Posts</legend>
              <div className="flex flex-wrap gap-x-6 gap-y-2">
                {(
                  [
                    ['all', 'All posts & reels'],
                    ['specific', 'Specific posts'],
                    ['next', 'My next post'],
                  ] as const
                ).map(([value, label]) => (
                  <label key={value} className="flex cursor-pointer items-center gap-2 text-sm text-slate-800">
                    <input
                      type="radio"
                      name={`${id}-posts`}
                      checked={trigger.postsMode === value}
                      onChange={() =>
                        onChange(
                          value === 'next' && !trigger.next_post_word
                            ? { postsMode: value, next_post_word: trigger.keywords[0] ?? '' }
                            : { postsMode: value },
                        )
                      }
                      className="size-4 accent-indigo-600"
                    />
                    {label}
                  </label>
                ))}
              </div>
              {trigger.postsMode === 'next' && (
                <Field
                  label="Word in the caption"
                  htmlFor={`${id}-next-word`}
                  error={postsError}
                  hint="Put this word in the scheduled post’s caption, e.g. “Comment CLONE”. When the post goes live, FlowDM links this automation to it within 2 minutes, even with your laptop off. Older posts are never touched."
                >
                  <input
                    id={`${id}-next-word`}
                    value={trigger.next_post_word}
                    onChange={(e) => onChange({ next_post_word: e.target.value })}
                    placeholder="e.g. CLONE"
                    className={cn('input max-w-xs', postsError && 'input-error')}
                  />
                </Field>
              )}
              {trigger.postsMode === 'specific' && (
                <>
                  <MediaPicker
                    accountId={accountId}
                    selected={trigger.media_ids}
                    onChange={(media_ids) => onChange({ media_ids })}
                    invalid={!!postsError}
                  />
                  {postsError && <p className="text-xs font-medium text-red-600">{postsError}</p>}
                </>
              )}
            </fieldset>

            {/* public replies */}
            <div>
              <div className="mb-1.5 text-sm font-medium text-slate-700">Public reply to the comment</div>
              <p className="mb-2 text-xs text-slate-500">One is picked at random. Leave empty for no public reply.</p>
              <div className="space-y-2">
                {trigger.public_replies.map((reply, i) => (
                  <div key={i} className="flex items-start gap-2">
                    <textarea
                      rows={1}
                      value={reply}
                      onChange={(e) =>
                        onChange({ public_replies: trigger.public_replies.map((r, j) => (j === i ? e.target.value : r)) })
                      }
                      placeholder="Sent you a DM! 📩"
                      aria-label={`Public reply ${i + 1}`}
                      className="input min-h-10 resize-y"
                    />
                    <IconButton
                      label={`Remove reply ${i + 1}`}
                      variant="danger-ghost"
                      size="md"
                      onClick={() => onChange({ public_replies: trigger.public_replies.filter((_, j) => j !== i) })}
                    >
                      <Trash2 className="size-4" />
                    </IconButton>
                  </div>
                ))}
              </div>
              <Button
                size="sm"
                variant="ghost"
                className="mt-1.5 -ml-2 text-indigo-600 hover:text-indigo-700"
                icon={<Plus className="size-4" />}
                onClick={() => onChange({ public_replies: [...trigger.public_replies, ''] })}
              >
                Add reply variant
              </Button>
            </div>

            {/* options */}
            <div className="space-y-1 border-t border-slate-100 pt-4">
              <Checkbox
                checked={trigger.once_per_user}
                onChange={(once_per_user) => onChange({ once_per_user })}
                label="Only DM each person once"
                description="If they comment again, they won't get the DM a second time."
              />
              <Checkbox
                checked={trigger.top_level_only}
                onChange={(top_level_only) => onChange({ top_level_only })}
                label="Ignore replies to other comments"
                description="Only top-level comments start the automation."
              />
            </div>
          </>
        )}
      </CardBody>
    </Card>
  )
}
