import { useEffect, useState } from 'react'
import type { LocalEmbeddingStatus } from '../../types/electron'

export function useLocalEmbedding(): {
  localEmbedding: LocalEmbeddingStatus | null
  localEmbeddingError: string | null
  localEmbeddingToggling: boolean
  refreshLocalEmbedding: () => Promise<void>
  handleDownloadLocalEmbedding: () => Promise<void>
  handleToggleLocalEmbedding: () => Promise<void>
} {
  const [localEmbedding, setLocalEmbedding] = useState<LocalEmbeddingStatus | null>(null)
  const [localEmbeddingError, setLocalEmbeddingError] = useState<string | null>(null)
  const [localEmbeddingToggling, setLocalEmbeddingToggling] = useState(false)

  useEffect(() => {
    window.api.localEmbedding
      .status()
      .then(setLocalEmbedding)
      .catch((e) => setLocalEmbeddingError(String(e)))
  }, [])

  const refreshLocalEmbedding = async (): Promise<void> => {
    setLocalEmbeddingError(null)
    try {
      setLocalEmbedding(await window.api.localEmbedding.status())
    } catch (e) {
      setLocalEmbeddingError(String(e))
    }
  }

  const handleDownloadLocalEmbedding = async (): Promise<void> => {
    setLocalEmbeddingError(null)
    try {
      setLocalEmbedding(await window.api.localEmbedding.download())
    } catch (e) {
      setLocalEmbeddingError(String(e))
    }
  }

  useEffect(() => {
    if (localEmbedding?.download_status !== 'downloading') return
    const interval = setInterval(refreshLocalEmbedding, 3000)
    return () => clearInterval(interval)
  }, [localEmbedding?.download_status])

  const handleToggleLocalEmbedding = async (): Promise<void> => {
    if (!localEmbedding) return
    setLocalEmbeddingToggling(true)
    setLocalEmbeddingError(null)
    try {
      const next = await window.api.localEmbedding.setEnabled(!localEmbedding.enabled)
      setLocalEmbedding(next)
    } catch (e) {
      setLocalEmbeddingError(String(e))
    } finally {
      setLocalEmbeddingToggling(false)
    }
  }

  return {
    localEmbedding,
    localEmbeddingError,
    localEmbeddingToggling,
    refreshLocalEmbedding,
    handleDownloadLocalEmbedding,
    handleToggleLocalEmbedding,
  }
}
