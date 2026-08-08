import { useEffect, useState } from 'react'
import type { GpuRerankerStatus } from '../../types/electron'

export function useGpuReranker(): {
  gpuReranker: GpuRerankerStatus | null
  gpuRerankerError: string | null
  gpuRerankerToggling: boolean
  refreshGpuReranker: () => Promise<void>
  handleDownloadGpuReranker: () => Promise<void>
  handleToggleGpuReranker: () => Promise<void>
} {
  const [gpuReranker, setGpuReranker] = useState<GpuRerankerStatus | null>(null)
  const [gpuRerankerError, setGpuRerankerError] = useState<string | null>(null)
  const [gpuRerankerToggling, setGpuRerankerToggling] = useState(false)

  useEffect(() => {
    window.api.gpuReranker
      .status()
      .then(setGpuReranker)
      .catch((e) => setGpuRerankerError(String(e)))
  }, [])

  const refreshGpuReranker = async (): Promise<void> => {
    setGpuRerankerError(null)
    try {
      setGpuReranker(await window.api.gpuReranker.status())
    } catch (e) {
      setGpuRerankerError(String(e))
    }
  }

  const handleDownloadGpuReranker = async (): Promise<void> => {
    setGpuRerankerError(null)
    try {
      setGpuReranker(await window.api.gpuReranker.download())
    } catch (e) {
      setGpuRerankerError(String(e))
    }
  }

  // Poll while a download is in progress — starts automatically once
  // download_status flips to 'downloading' (whether from clicking Download or
  // from a status fetch that finds one already in flight from a prior session).
  useEffect(() => {
    if (gpuReranker?.download_status !== 'downloading') return
    const interval = setInterval(refreshGpuReranker, 3000)
    return () => clearInterval(interval)
  }, [gpuReranker?.download_status])

  const handleToggleGpuReranker = async (): Promise<void> => {
    if (!gpuReranker) return
    setGpuRerankerToggling(true)
    setGpuRerankerError(null)
    try {
      const next = await window.api.gpuReranker.setEnabled(!gpuReranker.enabled)
      setGpuReranker(next)
    } catch (e) {
      setGpuRerankerError(String(e))
    } finally {
      setGpuRerankerToggling(false)
    }
  }

  return {
    gpuReranker,
    gpuRerankerError,
    gpuRerankerToggling,
    refreshGpuReranker,
    handleDownloadGpuReranker,
    handleToggleGpuReranker,
  }
}
