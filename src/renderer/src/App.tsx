import React, { Suspense, lazy } from 'react'
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { ErrorBoundary } from './components/ui/ErrorBoundary'
import { Skeleton } from './components/ui/LoadingSkeleton'
import { ToastContainer } from './components/ui'

const SearchScreen = lazy(() => import('./screens/search').then(m => ({ default: m.SearchScreen })))
const ProvidersSetup = lazy(() => import('./screens/providers'))
const CodeHostsSetup = lazy(() => import('./screens/code-hosts'))
const RepositoriesScreen = lazy(() => import('./screens/repositories'))
const SnapshotViewerScreen = lazy(() => import('./screens/snapshot-viewer'))
const IndexOverviewScreen = lazy(() => import('./screens/index-overview'))
const GraphScreen = lazy(() => import('./screens/graph'))
const DocParsingScreen = lazy(() => import('./screens/doc-parsing'))
const DocCodeValidationScreen = lazy(() => import('./screens/doc-code-validation').then(m => ({ default: m.DocCodeValidationScreen })))
const LinkedGraphScreen = lazy(() => import('./screens/linked-graph').then(m => ({ default: m.LinkedGraphScreen })))
const SettingsScreen = lazy(() => import('./screens/settings'))

function PageFallback(): React.ReactElement {
  return (
    <div className="p-6 space-y-4">
      <Skeleton className="h-6 w-40" />
      <Skeleton className="h-4 w-64" />
      <Skeleton className="h-32 w-full" />
    </div>
  )
}

export default function App(): React.ReactElement {
  return (
    <ErrorBoundary>
      <ToastContainer />
      <HashRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AppShell>
          <Suspense fallback={<PageFallback />}>
            <Routes>
              <Route path="/" element={<Navigate to="/search" replace />} />
              <Route path="/search" element={<SearchScreen />} />
              <Route path="/repositories" element={<RepositoriesScreen />} />
              <Route path="/providers" element={<ProvidersSetup />} />
              <Route path="/code-hosts" element={<CodeHostsSetup />} />
              <Route path="/snapshot-viewer" element={<SnapshotViewerScreen />} />
              <Route path="/index-overview" element={<IndexOverviewScreen />} />
              <Route path="/graph" element={<GraphScreen />} />
              <Route path="/doc-parsing" element={<DocParsingScreen />} />
              <Route path="/doc-code-validation" element={<DocCodeValidationScreen />} />
              <Route path="/linked-graph/*" element={<LinkedGraphScreen />} />
              <Route path="/settings" element={<SettingsScreen />} />

              {/* Legacy / fallback redirects */}
              <Route path="/ca/*" element={<Navigate to="/repositories" replace />} />
              <Route path="/aeh/*" element={<Navigate to="/repositories" replace />} />
            </Routes>
          </Suspense>
        </AppShell>
      </HashRouter>
    </ErrorBoundary>
  )
}
