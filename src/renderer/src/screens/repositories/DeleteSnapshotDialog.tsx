import React from 'react'
import { ConfirmDialog } from '../../components/ui'

interface DeleteSnapshotDialogProps {
  open: boolean
  onClose: () => void
  onConfirm: () => void
  loading: boolean
}

export function DeleteSnapshotDialog({ open, onClose, onConfirm, loading }: DeleteSnapshotDialogProps): React.ReactElement {
  return (
    <ConfirmDialog
      open={open}
      onClose={onClose}
      onConfirm={onConfirm}
      title="Delete snapshot?"
      description="This will remove the snapshot and related index artifacts (manifest, symbols, graph)."
      confirmLabel="Delete"
      confirmVariant="danger"
      loading={loading}
    />
  )
}
