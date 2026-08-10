import { ipcMain } from 'electron'
import type { BackendClient } from '../infrastructure/python-server/client'

export function registerDocGraphHandlers(client: BackendClient): void {
  ipcMain.handle('docGraph:build', (_event, body: { source_dir: string; snapshot_id?: string | null; force_rebuild?: boolean }) =>
    client.post('/api/doc-graph/build', body)
  )

  ipcMain.handle('docGraph:summary', (_event, clusterId: string) =>
    client.get(`/api/doc-graph/summary/${clusterId}`)
  )

  ipcMain.handle('docGraph:nodes', (_event, clusterId: string, limit = 500, offset = 0, nodeType?: string) => {
    const qs = new URLSearchParams()
    qs.set('limit', String(limit))
    qs.set('offset', String(offset))
    if (nodeType) qs.set('node_type', nodeType)
    return client.get(`/api/doc-graph/nodes/${clusterId}?${qs.toString()}`)
  })

  ipcMain.handle('docGraph:edges', (_event, clusterId: string, limit = 2000, offset = 0, edgeType?: string) => {
    const qs = new URLSearchParams()
    qs.set('limit', String(limit))
    qs.set('offset', String(offset))
    if (edgeType) qs.set('edge_type', edgeType)
    return client.get(`/api/doc-graph/edges/${clusterId}?${qs.toString()}`)
  })

  ipcMain.handle('docGraph:mismatches', (_event, clusterId: string, severity?: string) => {
    const qs = new URLSearchParams()
    if (severity) qs.set('severity', severity)
    return client.get(`/api/doc-graph/mismatches/${clusterId}?${qs.toString()}`)
  })

  ipcMain.handle('docGraph:exportJson', (_event, clusterId: string) =>
    client.get(`/api/doc-graph/export/${clusterId}`)
  )

  ipcMain.handle('docGraph:listClusters', () =>
    client.get('/api/doc-graph/clusters')
  )

  ipcMain.handle('docGraph:deleteCluster', (_e, clusterId: string) =>
    client.del(`/api/doc-graph/clusters/${clusterId}`)
  )

  ipcMain.handle('docGraph:pickFiles', async () => {
    const dialog = await import('electron').then((m) => m.dialog)
    const r = await dialog.showOpenDialog({
      properties: ['openFile', 'multiSelections'],
      filters: [{ name: 'Markdown', extensions: ['md'] }]
    })
    return r.canceled ? [] : r.filePaths
  })

  ipcMain.handle(
    'docGraph:stageAndBuild',
    async (
      _e,
      body: { files: string[]; snapshot_id?: string | null; force_rebuild?: boolean; llm_enabled?: boolean }
    ) => {
      const fs = await import('node:fs/promises')
      const os = await import('node:os')
      const path = await import('node:path')
      const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'emt-docgraph-'))
      for (const f of body.files) {
        await fs.copyFile(f, path.join(dir, path.basename(f)))
      }
      return client.post(
        '/api/doc-graph/build',
        {
          source_dir: dir,
          snapshot_id: body.snapshot_id ?? null,
          force_rebuild: body.force_rebuild ?? true,
          llm_enabled: body.llm_enabled ?? true
        },
        20 * 60 * 1000
      )
    }
  )

  ipcMain.handle(
    'docGraph:buildStream',
    async (
      _e,
      body: { files: string[]; snapshot_id?: string | null; force_rebuild?: boolean; llm_enabled?: boolean }
    ) => {
      const fs = await import('node:fs/promises')
      const os = await import('node:os')
      const path = await import('node:path')
      const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'emt-docgraph-'))
      for (const f of body.files) {
        await fs.copyFile(f, path.join(dir, path.basename(f)))
      }
      await client.postStream(
        '/api/doc-graph/build-stream',
        {
          source_dir: dir,
          snapshot_id: body.snapshot_id ?? null,
          force_rebuild: body.force_rebuild ?? true,
          llm_enabled: body.llm_enabled ?? true
        },
        (evt) => {
          _e.sender.send('docGraph:stream', evt)
        }
      )
      return { ok: true }
    }
  )
}
