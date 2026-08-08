import { useEffect, useState } from 'react'
import { KeyRound, ChevronDown, X } from 'lucide-react'
import { Button } from '../../components/ui'

export function SshKeySettings() {
  const [keyPath, setKeyPath] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [draft, setDraft] = useState<string | null>(null)

  useEffect(() => {
    window.api.git.getConfig().then((cfg) => {
      setKeyPath(cfg.ssh_key_path)
      setDraft(cfg.ssh_key_path)
    })
  }, [])

  const handlePick = async () => {
    const picked = await window.api.git.pickSshKey()
    if (picked) setDraft(picked)
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const result = await window.api.git.setConfig(draft ?? null)
      setKeyPath(result.ssh_key_path)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } finally {
      setSaving(false)
    }
  }

  const handleClear = async () => {
    setSaving(true)
    try {
      await window.api.git.setConfig(null)
      setKeyPath(null)
      setDraft(null)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } finally {
      setSaving(false)
    }
  }

  const isDirty = draft !== keyPath

  return (
    <div className="border border-zinc-800 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2.5 px-4 py-3 text-left hover:bg-zinc-800/40 transition-colors"
      >
        <KeyRound size={14} className="text-zinc-500 shrink-0" />
        <span className="text-xs font-medium text-zinc-300 flex-1">SSH Key for Git Clone</span>
        {keyPath && (
          <span className="text-xs text-emerald-400 font-mono truncate max-w-[180px]">
            {keyPath.split(/[\\/]/).pop()}
          </span>
        )}
        {!keyPath && (
          <span className="text-xs text-zinc-600">not set — uses system default</span>
        )}
        <ChevronDown
          size={13}
          className={`text-zinc-600 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3 border-t border-zinc-800">
          <p className="text-xs text-zinc-500 pt-3 leading-relaxed">
            Used for <code className="text-zinc-300 bg-zinc-800 px-1 rounded">git@</code> and{' '}
            <code className="text-zinc-300 bg-zinc-800 px-1 rounded">ssh://</code> URLs.
            If not set, git uses your system SSH agent or default{' '}
            <code className="text-zinc-300 bg-zinc-800 px-1 rounded">~/.ssh/id_*</code> keys.
          </p>

          <div className="flex items-center gap-2">
            <input
              type="text"
              value={draft ?? ''}
              onChange={(e) => setDraft(e.target.value || null)}
              placeholder="~/.ssh/id_ed25519"
              className="flex-1 px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-lg text-xs text-zinc-300 placeholder-zinc-600 focus:outline-none focus:border-zinc-500 font-mono transition-colors"
            />
            <button
              onClick={handlePick}
              className="px-3 py-2 text-xs text-zinc-400 hover:text-zinc-200 border border-zinc-700 hover:border-zinc-500 rounded-lg transition-colors whitespace-nowrap"
            >
              Browse…
            </button>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              onClick={handleSave}
              disabled={!isDirty || saving}
              loading={saving}
            >
              {saved ? 'Saved' : 'Save'}
            </Button>
            {keyPath && (
              <Button
                variant="secondary"
                size="sm"
                onClick={handleClear}
                disabled={saving}
              >
                <X size={12} />
                Clear
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
