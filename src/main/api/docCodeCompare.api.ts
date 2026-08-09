import { ipcMain } from 'electron'
import type { BackendClient } from '../infrastructure/python-server/client'

export function registerDocCodeCompareHandlers(client: BackendClient): void {
  ipcMain.handle('docCode:compare', (_e, body: { cluster_id: string; snapshot_id: string }) =>
    client.post('/api/doc-code/compare', body)
  )

  ipcMain.handle(
    'docCode:compareRelations',
    (_e, body: { cluster_id: string; snapshot_id: string }) =>
      client.post('/api/doc-code/compare-relations', body)
  )
}
