import type { ProviderConfig } from '../../types/electron'

export type AddKind = string | null

export interface ProviderFormProps {
  kind: 'ollama' | 'lmstudio' | 'openai' | 'anthropic' | 'gemini' | 'deepseek' | 'openrouter'
  initial?: ProviderConfig
  onClose: () => void
}

export interface CloudSectionProps {
  kind: 'openai' | 'anthropic' | 'gemini' | 'deepseek' | 'openrouter'
  title: string
  description: string
  providers: ProviderConfig[]
  adding: string | null
  onAdd: (kind: string) => void
  onCloseAdd: () => void
}
