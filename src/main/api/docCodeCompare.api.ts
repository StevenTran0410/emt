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

  ipcMain.handle(
    'docCode:assess',
    (_e, body: { cluster_id: string; snapshot_id: string; provider_id?: string }) =>
      client.post('/api/doc-code/assess', body)
  )

  ipcMain.handle(
    'docCode:getAssessment',
    (_e, params: { cluster_id: string; snapshot_id: string }) =>
      client.get(`/api/doc-code/assessment?cluster_id=${params.cluster_id}&snapshot_id=${params.snapshot_id}`)
  )
}
