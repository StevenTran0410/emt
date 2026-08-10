import React, { useEffect } from 'react'
import { X, BookOpen } from 'lucide-react'
import { MethodologyTable } from './MethodologyTable'

interface MethodologyDrawerProps {
  isOpen: boolean
  onClose: () => void
  initialDimensionId?: string
}

export function MethodologyDrawer({
  isOpen,
  onClose,
  initialDimensionId
}: MethodologyDrawerProps): React.ReactElement | null {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose])

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 overflow-hidden animate-in fade-in duration-200">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-zinc-950/70 backdrop-blur-sm transition-opacity"
        onClick={onClose}
      />

      {/* Right-anchored slide-over panel */}
      <div className="fixed inset-y-0 right-0 max-w-full flex pl-10">
        <div className="w-screen max-w-2xl bg-zinc-950 border-l border-zinc-800 shadow-2xl flex flex-col transform transition-transform duration-300">
          {/* Header */}
          <div className="px-6 py-4 border-b border-zinc-800 bg-zinc-900/80 flex items-center justify-between shrink-0">
            <div className="flex items-center gap-2.5">
              <div className="p-2 rounded-lg bg-indigo-500/10 border border-indigo-500/30 text-indigo-400">
                <BookOpen className="w-5 h-5" />
              </div>
              <div>
                <h2 className="font-bold text-zinc-100 text-base">Doc↔Code Validation Methodology</h2>
                <p className="text-xs text-zinc-400">
                  Two-dimensional framework for technical specification audit & completeness verification
                </p>
              </div>
            </div>

            <button
              onClick={onClose}
              className="p-1.5 rounded-lg bg-zinc-900 hover:bg-zinc-800 text-zinc-400 hover:text-zinc-100 border border-zinc-800 transition-colors"
              title="Close drawer (Esc)"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto p-6">
            <MethodologyTable initialDimensionId={initialDimensionId} />
          </div>
        </div>
      </div>
    </div>
  )
}
