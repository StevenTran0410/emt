import { useState } from 'react'
import { BookOpen, ChevronDown, ChevronRight } from 'lucide-react'

export function LMStudioSetupGuide() {
  const [open, setOpen] = useState(false)
  return (
    <div className="border border-zinc-700/60 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-4 py-3 text-xs text-zinc-400 hover:text-zinc-300 hover:bg-zinc-800/40 transition-colors"
      >
        <BookOpen size={13} className="text-sky-400" />
        <span className="flex-1 text-left font-medium">How to enable LM Studio Local Server</span>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
      </button>
      {open && (
        <div className="px-4 pb-4 space-y-3 bg-zinc-800/20">
          <ol className="space-y-2 text-xs text-zinc-400 list-none">
            <li className="flex gap-3">
              <span className="shrink-0 w-5 h-5 rounded-full bg-sky-500/20 text-sky-400 flex items-center justify-center text-[10px] font-bold">1</span>
              <span>Open LM Studio → click the <span className="text-zinc-200 font-mono bg-zinc-700 px-1 rounded">↔</span> <strong className="text-zinc-300">Local Server</strong> tab in the left sidebar.</span>
            </li>
            <li className="flex gap-3">
              <span className="shrink-0 w-5 h-5 rounded-full bg-sky-500/20 text-sky-400 flex items-center justify-center text-[10px] font-bold">2</span>
              <span>Select a model from the dropdown at the top of the server tab, then click <strong className="text-zinc-300">Start Server</strong>.</span>
            </li>
            <li className="flex gap-3">
              <span className="shrink-0 w-5 h-5 rounded-full bg-sky-500/20 text-sky-400 flex items-center justify-center text-[10px] font-bold">3</span>
              <span>Default port is <span className="font-mono text-sky-400">1234</span>. Leave the base URL as <span className="font-mono text-zinc-300">http://localhost:1234</span> unless you changed it.</span>
            </li>
            <li className="flex gap-3">
              <span className="shrink-0 w-5 h-5 rounded-full bg-sky-500/20 text-sky-400 flex items-center justify-center text-[10px] font-bold">4</span>
              <span>Save the provider here, then click <strong className="text-zinc-300">Test connection</strong> — you should see the loaded model appear in Browse.</span>
            </li>
          </ol>
        </div>
      )}
    </div>
  )
}
