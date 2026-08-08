import { useEffect, useRef, useState } from 'react'
import type { AddKind } from './types'

// Gates cloud-provider "Add" actions behind a one-time BYOK consent banner.
export function useConsentGate(setAdding: (kind: AddKind) => void) {
  const [consentGiven, setConsentGiven] = useState<boolean | null>(null)
  const [showConsent, setShowConsent] = useState(false)
  const pendingKindRef = useRef<string | null>(null)

  useEffect(() => {
    window.api.consent.checkCloud().then((r) => setConsentGiven(r.given)).catch(() => setConsentGiven(false))
  }, [])

  const handleAddCloud = (kind: string) => {
    if (!consentGiven) {
      pendingKindRef.current = kind
      setShowConsent(true)
    } else {
      setAdding(kind)
    }
  }

  const handleConsentAccept = async () => {
    await window.api.consent.giveCloud(true)
    setConsentGiven(true)
    setShowConsent(false)
    if (pendingKindRef.current) {
      setAdding(pendingKindRef.current)
      pendingKindRef.current = null
    }
  }

  const dismissConsent = () => {
    setShowConsent(false)
    pendingKindRef.current = null
  }

  return { showConsent, handleAddCloud, handleConsentAccept, dismissConsent }
}
