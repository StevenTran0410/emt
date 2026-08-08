import React, { useState } from 'react'
import { RefreshCw } from 'lucide-react'

export function WeightsDownloadNotice({
  downloadUrl,
  weightsDir,
  modelLabel,
  downloadStatus,
  downloadError,
  onDownload,
}: {
  downloadUrl: string
  weightsDir: string
  modelLabel: string
  downloadStatus: 'idle' | 'downloading' | 'done' | 'failed'
  downloadError: string | null
  onDownload: () => void
}): React.ReactElement {
  const [copied, setCopied] = useState(false)
  const handleCopy = (): void => {
    navigator.clipboard.writeText(weightsDir).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }
  return (
    <div className="space-y-2 py-2 px-3 border border-amber-800/40 bg-amber-950/20 rounded-md">
      <p className="text-xs text-amber-200/90">
        Model weights not found for {modelLabel}. This only downloads when you click below — the
        app never fetches it automatically.
      </p>
      {downloadStatus === 'failed' && downloadError && (
        <p className="text-[11px] text-red-400">Download failed: {downloadError}</p>
      )}
      <button
        onClick={onDownload}
        disabled={downloadStatus === 'downloading'}
        className="flex items-center gap-1.5 text-[11px] text-white bg-blue-600/90 hover:bg-blue-600 disabled:opacity-50 rounded px-3 py-1.5 transition-colors"
      >
        {downloadStatus === 'downloading' && <RefreshCw className="w-3 h-3 animate-spin" />}
        {downloadStatus === 'downloading'
          ? 'Downloading…'
          : downloadStatus === 'failed'
            ? 'Retry download'
            : 'Download model weights'}
      </button>
      <div className="flex items-center gap-2 pt-1">
        <p className="text-[10px] text-gray-500 shrink-0">Or place files manually in:</p>
        <code className="flex-1 text-[10px] text-gray-400 bg-surface-raised border border-surface-border rounded px-2 py-1 overflow-x-auto whitespace-nowrap">
          {weightsDir}
        </code>
        <button
          onClick={handleCopy}
          className="shrink-0 text-[10px] text-gray-400 hover:text-gray-200 border border-surface-border rounded px-2 py-1 transition-colors"
        >
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>
      <p className="text-[10px] text-gray-600">
        Model card:{' '}
        <a href={downloadUrl} target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline">
          {downloadUrl}
        </a>
      </p>
    </div>
  )
}
