import React from 'react'
import { Cpu, RefreshCw } from 'lucide-react'
import type { GpuRerankerStatus } from '../../types/electron'
import { WeightsDownloadNotice } from './WeightsDownloadNotice'

export function GpuRerankerSection({
  gpuReranker,
  gpuRerankerError,
  gpuRerankerToggling,
  refreshGpuReranker,
  handleDownloadGpuReranker,
  handleToggleGpuReranker,
}: {
  gpuReranker: GpuRerankerStatus | null
  gpuRerankerError: string | null
  gpuRerankerToggling: boolean
  refreshGpuReranker: () => void
  handleDownloadGpuReranker: () => void
  handleToggleGpuReranker: () => void
}): React.ReactElement {
  return (
    <section className="card p-4 space-y-3">
      <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
        <Cpu className="w-4 h-4" />
        GPU Reranker
      </h2>
      {gpuRerankerError && (
        <p className="text-xs text-red-400">{gpuRerankerError}</p>
      )}
      {!gpuReranker ? (
        <p className="text-xs text-gray-500">Checking GPU availability...</p>
      ) : !gpuReranker.gpu_available ? (
        <>
          <p className="text-xs text-gray-500">
            No GPU with at least 2GB VRAM was detected. The cross-encoder reranking
            stage requires a CUDA-capable GPU and is unavailable on this machine.
          </p>
          <div className="flex items-center justify-between py-2 border border-surface-border rounded-md px-3 opacity-50">
            <span className="text-sm text-gray-400">Enable GPU reranker</span>
            <span className="text-xs text-gray-500 bg-surface-raised border border-surface-border px-2 py-0.5 rounded-full">
              Unavailable
            </span>
          </div>
        </>
      ) : (
        <>
          <p className="text-xs text-gray-500">
            Rescores retrieval results using a local cross-encoder model
            (jina-reranker-v3, {gpuReranker.vram_gb}GB VRAM detected). When enabled,
            every query is reranked; when disabled, retrieval stops at RRF fusion.
          </p>
          <p className="text-xs text-amber-500/80">
            This model is licensed CC BY-NC-4.0 (non-commercial). See the model card
            on Hugging Face (jinaai/jina-reranker-v3) before enabling in a commercial context.
          </p>
          {!gpuReranker.weights_ready && (
            <WeightsDownloadNotice
              downloadUrl={gpuReranker.download_url}
              weightsDir={gpuReranker.weights_dir}
              modelLabel="jina-reranker-v3"
              downloadStatus={gpuReranker.download_status}
              downloadError={gpuReranker.download_error}
              onDownload={handleDownloadGpuReranker}
            />
          )}
          <div className="flex items-center gap-2">
            <button
              onClick={handleToggleGpuReranker}
              disabled={gpuRerankerToggling || (!gpuReranker.enabled && !gpuReranker.weights_ready)}
              className="flex-1 flex items-center justify-between py-2 border border-surface-border rounded-md px-3 hover:border-gray-600 transition-colors disabled:opacity-50"
            >
              <span className="text-sm text-gray-400">Enable GPU reranker</span>
              <span
                className={
                  gpuReranker.enabled
                    ? 'text-xs text-green-400 bg-green-950 border border-green-800 px-2 py-0.5 rounded-full'
                    : 'text-xs text-gray-400 bg-surface-raised border border-surface-border px-2 py-0.5 rounded-full'
                }
              >
                {gpuRerankerToggling ? 'Updating...' : gpuReranker.enabled ? 'On' : 'Off'}
              </span>
            </button>
            {!gpuReranker.weights_ready && (
              <button
                onClick={refreshGpuReranker}
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
