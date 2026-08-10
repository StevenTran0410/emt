import { contextBridge, ipcRenderer } from 'electron'

// Bridge for the renderer's `window.api.*` surface. Every method maps 1:1 to an
// ipcMain.handle channel registered in src/main/api/*. Namespaces whose main-side
// handlers are not part of this standalone carve-out (consent, job, qa, impact,
// aeh) are stubbed with quiet, shape-compatible defaults so screens that touch
// them degrade instead of crashing.

const invoke = (channel: string) =>
  (...args: unknown[]) => ipcRenderer.invoke(channel, ...args)

type SectionDoneHandler = (evt: unknown) => void
const sectionDoneListeners = new Map<SectionDoneHandler, (e: unknown, evt: unknown) => void>()

type DocGraphStreamHandler = (evt: unknown) => void
const docGraphStreamListeners = new Map<DocGraphStreamHandler, (e: unknown, evt: unknown) => void>()

const api = {
  app: {
    getVersion: invoke('app:get-version'),
    getUserDataPath: invoke('app:get-user-data-path'),
    getLogsPath: invoke('app:get-logs-path'),
    getDiagnostics: invoke('app:get-diagnostics'),
    copyToClipboard: invoke('app:copy-to-clipboard'),
    showInFolder: invoke('app:show-in-folder'),
    retryBackend: invoke('app:retry-backend')
  },
  workspace: {
    list: invoke('workspace:list'),
    create: invoke('workspace:create'),
    rename: invoke('workspace:rename'),
    delete: invoke('workspace:delete')
  },
  provider: {
    list: invoke('provider:list'),
    create: invoke('provider:create'),
    update: invoke('provider:update'),
    delete: invoke('provider:delete'),
    test: invoke('provider:test'),
    models: invoke('provider:models'),
    embeddingModels: invoke('provider:embedding-models'),
    endpoints: invoke('provider:endpoints')
  },
  gpuReranker: {
    status: invoke('gpuReranker:status'),
    setEnabled: invoke('gpuReranker:setEnabled'),
    download: invoke('gpuReranker:download')
  },
  localEmbedding: {
    status: invoke('localEmbedding:status'),
    setEnabled: invoke('localEmbedding:setEnabled'),
    download: invoke('localEmbedding:download')
  },
  folder: {
    pick: invoke('folder:pick'),
    validate: invoke('folder:validate'),
    list: invoke('folder:list'),
    add: invoke('folder:add'),
    remove: invoke('folder:remove'),
    revalidate: invoke('folder:revalidate'),
    branches: invoke('folder:branches'),
    setBranch: invoke('folder:setBranch'),
    setActiveSnapshot: invoke('folder:setActiveSnapshot'),
    updateSettings: invoke('folder:updateSettings'),
    estimateFileCount: invoke('folder:estimateFileCount'),
    cloneFromUrl: invoke('folder:cloneFromUrl')
  },
  sync: {
    prepare: invoke('sync:prepare'),
    listForRepo: invoke('sync:listForRepo'),
    getSnapshot: invoke('sync:getSnapshot'),
    deleteSnapshot: invoke('sync:deleteSnapshot')
  },
  manifest: {
    build: invoke('manifest:build'),
    tree: invoke('manifest:tree'),
    file: invoke('manifest:file')
  },
  repomap: {
    build: invoke('repomap:build'),
    summary: invoke('repomap:summary'),
    symbols: invoke('repomap:symbols'),
    search: invoke('repomap:search'),
    exportCsv: invoke('repomap:exportCsv')
  },
  graph: {
    build: invoke('graph:build'),
    summary: invoke('graph:summary'),
    edges: invoke('graph:edges'),
    neighbors: invoke('graph:neighbors'),
    communities: invoke('graph:communities'),
    communityForNode: invoke('graph:communityForNode'),
    cycles: invoke('graph:cycles'),
    symbolEdges: invoke('graph:symbolEdges'),
    exportData: invoke('graph:exportData'),
    exportJson: invoke('graph:exportJson')
  },
  docGraph: {
    build: invoke('docGraph:build'),
    summary: invoke('docGraph:summary'),
    nodes: invoke('docGraph:nodes'),
    edges: invoke('docGraph:edges'),
    mismatches: invoke('docGraph:mismatches'),
    exportJson: invoke('docGraph:exportJson'),
    listClusters: invoke('docGraph:listClusters'),
    deleteCluster: invoke('docGraph:deleteCluster'),
    pickFiles: invoke('docGraph:pickFiles'),
    stageAndBuild: invoke('docGraph:stageAndBuild'),
    buildStream: invoke('docGraph:buildStream'),
    onStreamEvent: (handler: DocGraphStreamHandler) => {
      const listener = (_e: unknown, evt: unknown) => handler(evt)
      docGraphStreamListeners.set(handler, listener)
      ipcRenderer.on('docGraph:stream', listener)
    },
    offStreamEvent: (handler: DocGraphStreamHandler) => {
      const listener = docGraphStreamListeners.get(handler)
      if (listener) {
        ipcRenderer.removeListener('docGraph:stream', listener)
        docGraphStreamListeners.delete(handler)
      }
    }
  },
  docCode: {
    compare: invoke('docCode:compare'),
    compareRelations: invoke('docCode:compareRelations'),
    assess: invoke('docCode:assess'),
    getAssessment: invoke('docCode:getAssessment')
  },
  query: {
    exportCsv: invoke('query:exportCsv')
  },
  retrieval: {
    buildIndex: invoke('retrieval:buildIndex'),
    summary: invoke('retrieval:summary'),
    retrieve: invoke('retrieval:retrieve'),
    compare: invoke('retrieval:compare'),
    retrieveTwoStage: invoke('retrieval:retrieveTwoStage'),
    retrieveRrfFusion: invoke('retrieval:retrieveRrfFusion')
  },
  analysis: {
    estimate: invoke('analysis:estimate'),
    start: invoke('analysis:start'),
    listReports: invoke('analysis:listReports'),
    getReport: invoke('analysis:getReport'),
    getReportByJob: invoke('analysis:getReportByJob'),
    deleteReport: invoke('analysis:deleteReport'),
    exportReportMarkdown: invoke('analysis:exportReportMarkdown'),
    exportAuditSection: invoke('analysis:exportAuditSection'),
    rerunSection: invoke('analysis:rerunSection'),
    compareReports: invoke('analysis:compareReports'),
    getSectionSources: invoke('analysis:getSectionSources'),
    pollEvents: invoke('analysis:pollEvents'),
    getStaleness: invoke('analysis:getStaleness'),
    onSectionDone: (handler: SectionDoneHandler) => {
      const listener = (_e: unknown, evt: unknown) => handler(evt)
      sectionDoneListeners.set(handler, listener)
      ipcRenderer.on('analysis:section_done', listener as never)
    },
    offSectionDone: (handler: SectionDoneHandler) => {
      const listener = sectionDoneListeners.get(handler)
      if (listener) {
        ipcRenderer.removeListener('analysis:section_done', listener as never)
        sectionDoneListeners.delete(handler)
      }
    }
  },
  git: {
    getConfig: invoke('git:getConfig'),
    setConfig: invoke('git:setConfig'),
    pickSshKey: invoke('git:pickSshKey')
  },

  // ── Stubs: main-side handlers not included in the standalone carve-out ──
  consent: {
    // Single-user standalone build: cloud consent is implicitly granted.
    checkCloud: async () => ({ given: true }),
    giveCloud: async (_given: boolean) => ({ given: true })
  },
  job: {
    get: async (_jobId: string) => null,
    cancel: async (_jobId: string) => undefined,
    listForRepo: async (_repoId: string) => [],
    listRecent: async (_limit?: number) => []
  },
  qa: {
    classifier: async () => null
  },
  impact: {
    blastRadius: async (_req: unknown) => null
  },
  aeh: {
    listRuns: async () => [],
    listSiblingSystems: async () => [],
    getEvalRunCases: async () => [],
    start: async () => {
      throw new Error('AEH is not included in the standalone build')
    },
    judgeEvalRunCases: async () => {
      throw new Error('AEH is not included in the standalone build')
    },
    summarizeEvalRunAgent: async () => {
      throw new Error('AEH is not included in the standalone build')
    },
    traceDetail: async () => {
      throw new Error('AEH is not included in the standalone build')
    }
  }
}

contextBridge.exposeInMainWorld('api', api)
