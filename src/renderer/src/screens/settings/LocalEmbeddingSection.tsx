import React from 'react'
import { Cpu, RefreshCw } from 'lucide-react'
import type { LocalEmbeddingStatus } from '../../types/electron'
import { WeightsDownloadNotice } from './WeightsDownloadNotice'

export function LocalEmbeddingSection({
  localEmbedding,
  localEmbeddingError,
  localEmbeddingToggling,
  refreshLocalEmbedding,
  handleDownloadLocalEmbedding,
  handleToggleLocalEmbedding,
}: {
  localEmbedding: LocalEmbeddingStatus | null
  localEmbeddingError: string | null
  localEmbeddingToggling: boolean
  refreshLocalEmbedding: () => void
  handleDownloadLocalEmbedding: () => void
  handleToggleLocalEmbedding: () => void
}): React.ReactElement {
  return (
    <section className="card p-4 space-y-3">
      <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
        <Cpu className="w-4 h-4" />
        Local Embedding Model
      </h2>
      {localEmbeddingError && (
        <p className="text-xs text-red-400">{localEmbeddingError}</p>
      )}
      {!localEmbedding ? (
        <p className="text-xs text-gray-500">Checking GPU availability...</p>
      ) : !localEmbedding.gpu_available ? (
        <>
          <p className="text-xs text-gray-500">
            No GPU with at least 2GB VRAM was detected. The local embedding model
            stage requires a CUDA-capable GPU and is unavailable on this machine.
          </p>
          <div className="flex items-center justify-between py-2 border border-surface-border rounded-md px-3 opacity-50">
            <span className="text-sm text-gray-400">Enable local embedding model</span>
            <span className="text-xs text-gray-500 bg-surface-raised border border-surface-border px-2 py-0.5 rounded-full">
              Unavailable
            </span>
          </div>
        </>
      ) : (
        <>
          <p className="text-xs text-gray-500">
            Generates text embeddings locally using Qwen3-Embedding-0.6B on GPU
            ({localEmbedding.vram_gb}GB VRAM detected). When enabled, this model is
            loaded into VRAM for QA testset context clustering.
          </p>
          {!localEmbedding.weights_ready && (
            <WeightsDownloadNotice
              downloadUrl={localEmbedding.download_url}
              weightsDir={localEmbedding.weights_dir}
              modelLabel="Qwen3-Embedding-0.6B"
              downloadStatus={localEmbedding.download_status}
              downloadError={localEmbedding.download_error}
              onDownload={handleDownloadLocalEmbedding}
            />
          )}
          <div className="flex items-center gap-2">
            <button
              onClick={handleToggleLocalEmbedding}
              disabled={localEmbeddingToggling || (!localEmbedding.enabled && !localEmbedding.weights_ready)}
              className="flex-1 flex items-center justify-between py-2 border border-surface-border rounded-md px-3 hover:border-gray-600 transition-colors disabled:opacity-50"
            >
              <span className="text-sm text-gray-400">Enable local embedding model</span>
              <span
                className={
                  localEmbedding.enabled
                    ? 'text-xs text-green-400 bg-green-950 border border-green-800 px-2 py-0.5 rounded-full'
                    : 'text-xs text-gray-400 bg-surface-raised border border-surface-border px-2 py-0.5 rounded-full'
                }
              >
                {localEmbeddingToggling ? 'Updating...' : localEmbedding.enabled ? 'On' : 'Off'}
              </span>
            </button>
            {!localEmbedding.weights_ready && (
              <button
                onClick={refreshLocalEmbedding}
                title="Re-check for weights"
                className="shrink-0 h-9 w-9 flex items-center justify-center border border-surface-border rounded-md hover:border-gray-600 transition-colors text-gray-400 hover:text-gray-200"
              >
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </>
      )}
    </section>
  )
}
