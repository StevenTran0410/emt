import { useState, useCallback, useEffect } from 'react'
import type { AiAssessmentResult } from '../../types/electron'

export function useAiAssessment(clusterId: string, snapshotId: string) {
  const [assessment, setAssessment] = useState<AiAssessmentResult | null>(null)
  const [loading, setLoading] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)

  const fetchExistingAssessment = useCallback(async () => {
    if (!clusterId || !snapshotId) {
      setAssessment(null)
      return
    }
    try {
      const existing = await window.api.docCode.getAssessment({
        cluster_id: clusterId,
        snapshot_id: snapshotId
      })
      setAssessment(existing)
    } catch {
      // quiet fallback
      setAssessment(null)
    }
  }, [clusterId, snapshotId])

  useEffect(() => {
    fetchExistingAssessment()
  }, [fetchExistingAssessment])

  const runAssessment = useCallback(
    async (providerId?: string) => {
      if (!clusterId || !snapshotId) return
      // Guard against a click handler leaking a (non-cloneable) React event in here —
      // only a real string provider id may cross the IPC boundary.
      const provider = typeof providerId === 'string' ? providerId : undefined
      try {
        setLoading(true)
        setError(null)
        const result = await window.api.docCode.assess({
          cluster_id: clusterId,
          snapshot_id: snapshotId,
          provider_id: provider
        })
        setAssessment(result)
      } catch (err: any) {
        setError(err?.message || 'Failed to generate AI Assessment')
      } finally {
        setLoading(false)
      }
    },
    [clusterId, snapshotId]
  )

  return {
    assessment,
    loading,
    error,
    runAssessment,
    refreshAssessment: fetchExistingAssessment
  }
}
