import React, { useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import {
  Search,
  FolderOpen,
  Share2,
  Cpu,
  GitBranch,
  Settings,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Plus,
  Sun,
  Moon,
  Database,
  FileText,
  CheckCircle2,
  Network,
  ShieldCheck,
  Workflow
} from 'lucide-react'
import { useWorkspaceStore } from '../../store/workspace.store'
import { WorkspaceModal } from '../workspace/WorkspaceModal'
import { useTheme } from '../../hooks/useTheme'

const NAV_ITEMS = [
  { path: '/providers', label: 'Providers', icon: Cpu },
  { path: '/code-hosts', label: 'Code Hosts', icon: GitBranch },
  { path: '/repositories', label: 'Repositories', icon: FolderOpen },
  { path: '/doc-parsing', label: 'Document Parsing', icon: FileText },
  { path: '/bd-flow', label: 'BD Flow', icon: Workflow },
  { path: '/flow-integrity', label: 'Flow Integrity', icon: ShieldCheck },
  { path: '/doc-code-validation', label: 'Doc↔Code Validation', icon: CheckCircle2 },
  { path: '/linked-graph', label: 'Linked Multi-Graph', icon: Network },
  { path: '/search', label: 'Search', icon: Search },
  { path: '/graph', label: 'Graph', icon: Share2 },
]

export function Sidebar(): React.ReactElement {
  const { workspaces, activeWorkspaceId, setActive, create } = useWorkspaceStore()
  const [showWorkspacePicker, setShowWorkspacePicker] = useState(false)
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [isCollapsed, setIsCollapsed] = useState(() => localStorage.getItem('sidebar.collapsed') === '1')
  const navigate = useNavigate()
  const { isDark, toggle } = useTheme()

  const activeWorkspace = workspaces.find((w) => w.id === activeWorkspaceId)

  const toggleCollapse = () => {
    const newState = !isCollapsed
    setIsCollapsed(newState)
    localStorage.setItem('sidebar.collapsed', newState ? '1' : '0')
  }

  return (
    <>
      <aside className={`${isCollapsed ? 'w-14' : 'w-56'} shrink-0 flex flex-col bg-slate-900 border-r border-slate-800 transition-[width] duration-200 ease-in-out overflow-hidden`}>
        {/* App header */}
        <div className="h-14 flex items-center px-4 border-b border-slate-800">
          <Database className="w-5 h-5 text-indigo-400 shrink-0 mr-2" />
          {!isCollapsed && <span className="font-bold text-slate-100 text-sm tracking-wide truncate">Repo Retrieval Engine</span>}
        </div>

        {/* Workspace picker */}
        <div className="px-2 py-2 border-b border-slate-800/80">
          <button
            className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg bg-slate-800/50 hover:bg-slate-800 transition-colors text-left"
            onClick={() => setShowWorkspacePicker((v) => !v)}
            title={isCollapsed ? (activeWorkspace?.name ?? 'Select workspace') : undefined}
          >
            <div className="w-5 h-5 rounded bg-indigo-600 flex items-center justify-center shrink-0">
              <span className="text-white text-xs font-bold leading-none">
                {activeWorkspace?.name?.[0]?.toUpperCase() ?? '?'}
              </span>
            </div>
            {!isCollapsed && (
              <>
                <span className="text-xs font-medium text-slate-200 truncate flex-1 min-w-0">
                  {activeWorkspace?.name ?? 'Select workspace'}
                </span>
                <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
              </>
            )}
          </button>

          {showWorkspacePicker && !isCollapsed && (
            <div className="mt-1 rounded-lg border border-slate-700 bg-slate-800 shadow-xl overflow-hidden">
              {workspaces.map((ws) => (
                <button
                  key={ws.id}
                  className={`w-full text-left px-3 py-2 text-xs hover:bg-slate-700/60 transition-colors ${
                    ws.id === activeWorkspaceId ? 'text-indigo-400 font-semibold' : 'text-slate-300'
                  }`}
                  onClick={() => {
                    setActive(ws.id)
                    setShowWorkspacePicker(false)
                    navigate('/search')
                  }}
                >
                  {ws.name}
                </button>
              ))}
              <button
                className="w-full text-left px-3 py-2 text-xs text-slate-400 hover:bg-slate-700/60 flex items-center gap-1.5 border-t border-slate-700"
                onClick={() => {
                  setShowWorkspacePicker(false)
                  setShowCreateModal(true)
                }}
              >
                <Plus className="w-3.5 h-3.5" />
                New workspace
              </button>
            </div>
          )}
        </div>

        {/* Navigation items */}
        <nav className="flex-1 px-2 py-3 space-y-1 overflow-y-auto">
          {NAV_ITEMS.map(({ path, label, icon: Icon }) => (
            <NavLink
              key={path}
              to={path}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg text-xs font-medium transition-all ${
                  isActive
                    ? 'bg-indigo-600/20 text-indigo-400 border border-indigo-500/30'
                    : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
                }`
              }
              title={isCollapsed ? label : undefined}
            >
              <Icon className="w-4 h-4 shrink-0" />
              {!isCollapsed && <span>{label}</span>}
            </NavLink>
          ))}
        </nav>

        {/* Footer controls */}
        <div className="px-2 py-2 border-t border-slate-800 space-y-1">
          <button
            className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-200 transition-colors"
            onClick={toggleCollapse}
            title={isCollapsed ? 'Expand' : 'Collapse'}
          >
            {isCollapsed ? <ChevronRight className="w-4 h-4 shrink-0" /> : <ChevronLeft className="w-4 h-4 shrink-0" />}
            {!isCollapsed && <span>Collapse</span>}
          </button>
          
          <NavLink
            to="/settings"
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-lg text-xs font-medium transition-all ${
                isActive
                  ? 'bg-indigo-600/20 text-indigo-400 border border-indigo-500/30'
                  : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }`
            }
            title={isCollapsed ? 'Settings' : undefined}
          >
            <Settings className="w-4 h-4 shrink-0" />
            {!isCollapsed && <span>Settings</span>}
          </NavLink>

          <button
            className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-200 transition-colors"
            onClick={toggle}
            title={isCollapsed ? (isDark ? 'Light mode' : 'Dark mode') : undefined}
          >
            {isDark ? <Sun className="w-4 h-4 shrink-0" /> : <Moon className="w-4 h-4 shrink-0" />}
            {!isCollapsed && <span>{isDark ? 'Light mode' : 'Dark mode'}</span>}
          </button>
        </div>
      </aside>

      {showCreateModal && (
        <WorkspaceModal
          mode="create"
          onConfirm={(name, description) => create(name, description).then(() => {})}
          onClose={() => setShowCreateModal(false)}
        />
      )}
    </>
  )
}
