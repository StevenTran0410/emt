import { useEffect, useState } from 'react'
import type { Diagnostics } from './types'

export function useAppInfo(): {
  version: string
  userDataPath: string
  diagnostics: Diagnostics | null
  diagError: string | null
} {
  const [version, setVersion] = useState('')
  const [userDataPath, setUserDataPath] = useState('')
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null)
  const [diagError, setDiagError] = useState<string | null>(null)

  useEffect(() => {
    window.api.app.getVersion().then(setVersion).catch(() => {})
    window.api.app.getUserDataPath().then(setUserDataPath).catch(() => {})
    window.api.app.getDiagnostics()
      .then(setDiagnostics)
      .catch((e) => setDiagError(String(e)))
  }, [])

  return { version, userDataPath, diagnostics, diagError }
}
