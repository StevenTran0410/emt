import { ipcMain } from 'electron'
import type { BackendClient } from '../infrastructure/python-server/client'

export function registerUserFlowHandlers(client: BackendClient): void {
  ipcMain.handle('userFlow:pickFiles', async () => {
    const dialog = await import('electron').then((m) => m.dialog)
    const r = await dialog.showOpenDialog({
      properties: ['openFile', 'multiSelections'],
      filters: [{ name: 'Excel Spreadsheets', extensions: ['xlsx', 'xlsm', 'xls'] }]
    })
    return r.canceled ? [] : r.filePaths
  })

  ipcMain.handle('userFlow:listDocs', () => client.get('/api/user-flow/docs'))

  ipcMain.handle(
    'userFlow:import',
    (_event, body: { paths?: string[]; path?: string; provider_id?: string | null }) =>
      client.post('/api/user-flow/import', body, 20 * 60 * 1000)
  )

  ipcMain.handle(
    'userFlow:run',
    (_event, body: { doc_id: string; cluster_id: string; snapshot_id: string; provider_id?: string | null }) =>
      client.post('/api/user-flow/run', body, 20 * 60 * 1000)
  )

  ipcMain.handle(
    'userFlow:report',
    (_event, doc_id: string, cluster_id: string, snapshot_id: string) => {
      const qs = new URLSearchParams({ doc_id, cluster_id, snapshot_id })
      return client.get(`/api/user-flow/report?${qs.toString()}`)
    }
  )

  ipcMain.handle(
    'userFlow:graph',
    (_event, doc_id: string) => {
      const qs = new URLSearchParams({ doc_id })
      return client.get(`/api/user-flow/graph?${qs.toString()}`)
    }
  )
}
