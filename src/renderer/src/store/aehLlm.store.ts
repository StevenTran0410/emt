import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/**
 * Shared, persisted LLM selection for every AEH stage (Discovery / Map / Plan / Score).
 * One choice across all stages, remembered across app restarts (localStorage) so the user
 * never has to re-pick their provider/model each time.
 */
interface AehLlmState {
  providerId: string
  modelId: string
  reasoningEffort: string | null
  thinkingBudget: number | null
  setLlm: (
    c: Partial<Pick<AehLlmState, 'providerId' | 'modelId' | 'reasoningEffort' | 'thinkingBudget'>>
  ) => void
}

export const useAehLlmStore = create<AehLlmState>()(
  persist(
    (set) => ({
      providerId: '',
      modelId: '',
      reasoningEffort: null,
      thinkingBudget: null,
      setLlm: (c) => set(c),
    }),
    { name: 'aeh.llm.selection.v1' }
  )
)

type StrUpdater = string | ((prev: string) => string)

/**
 * The one LLM-picker binding every AEH stage uses — no per-stage boilerplate.
 * Destructure with aliases to keep a stage's existing variable names, e.g.
 * `const { providerId: selectedProviderId, setProviderId: setSelectedProviderId } = useAehLlmConfig()`.
 * providerId/modelId setters accept a value OR a React-style `(prev) => next` updater
 * (some stages seed the default via `setX((prev) => prev || first)`).
 */
export function useAehLlmConfig() {
  const providerId = useAehLlmStore((s) => s.providerId)
  const modelId = useAehLlmStore((s) => s.modelId)
  const reasoningEffort = useAehLlmStore((s) => s.reasoningEffort)
  const thinkingBudget = useAehLlmStore((s) => s.thinkingBudget)
  const setLlm = useAehLlmStore((s) => s.setLlm)

  const setProviderId = (v: StrUpdater): void =>
    setLlm({ providerId: typeof v === 'function' ? v(useAehLlmStore.getState().providerId) : v })
  const setModelId = (v: StrUpdater): void =>
    setLlm({ modelId: typeof v === 'function' ? v(useAehLlmStore.getState().modelId) : v })
  const setReasoningEffort = (v: string | null): void => setLlm({ reasoningEffort: v })
  const setThinkingBudget = (v: number | null): void => setLlm({ thinkingBudget: v })

  return {
    providerId,
    modelId,
    reasoningEffort,
    thinkingBudget,
    setProviderId,
    setModelId,
    setReasoningEffort,
    setThinkingBudget,
    setLlm,
  }
}
