import React from 'react'
import { ApplicationSection } from './ApplicationSection'
import { PrivacySection } from './PrivacySection'
import { StorageSection } from './StorageSection'
import { GpuRerankerSection } from './GpuRerankerSection'
import { LocalEmbeddingSection } from './LocalEmbeddingSection'
import { NativeEngineSection } from './NativeEngineSection'
import { useAppInfo } from './useAppInfo'
import { useGpuReranker } from './useGpuReranker'
import { useLocalEmbedding } from './useLocalEmbedding'

export default function SettingsScreen(): React.ReactElement {
  const { version, userDataPath, diagnostics, diagError } = useAppInfo()
  const gpuReranker = useGpuReranker()
  const localEmbedding = useLocalEmbedding()

  return (
    <>
      <div className="screen-header">
        <h1 className="screen-title">Settings</h1>
        <p className="screen-subtitle">App configuration, privacy defaults, and storage</p>
      </div>

      <div className="p-6 space-y-6 max-w-2xl">
        <ApplicationSection version={version} userDataPath={userDataPath} />

        <PrivacySection />

        <StorageSection />

        {/* ── GPU Reranker ─────────────────────────────────────────────────── */}
        <GpuRerankerSection
          gpuReranker={gpuReranker.gpuReranker}
          gpuRerankerError={gpuReranker.gpuRerankerError}
          gpuRerankerToggling={gpuReranker.gpuRerankerToggling}
          refreshGpuReranker={gpuReranker.refreshGpuReranker}
          handleDownloadGpuReranker={gpuReranker.handleDownloadGpuReranker}
          handleToggleGpuReranker={gpuReranker.handleToggleGpuReranker}
        />

        {/* ── Local Embedding Model ─────────────────────────────────────────── */}
        <LocalEmbeddingSection
          localEmbedding={localEmbedding.localEmbedding}
          localEmbeddingError={localEmbedding.localEmbeddingError}
          localEmbeddingToggling={localEmbedding.localEmbeddingToggling}
          refreshLocalEmbedding={localEmbedding.refreshLocalEmbedding}
          handleDownloadLocalEmbedding={localEmbedding.handleDownloadLocalEmbedding}
          handleToggleLocalEmbedding={localEmbedding.handleToggleLocalEmbedding}
        />

        <NativeEngineSection diagnostics={diagnostics} diagError={diagError} />
      </div>
    </>
  )
}
