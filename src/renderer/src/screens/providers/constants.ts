import type { CreateProviderRequest } from '../../types/electron'

// Model presets for cloud providers (shown when Browse is unavailable)
export const CLOUD_MODEL_PRESETS: Record<string, string[]> = {
  openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo', 'o3-mini'],
  anthropic: ['claude-opus-4-5', 'claude-sonnet-4-5', 'claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022', 'claude-3-haiku-20240307'],
  gemini: ['gemini-2.0-flash', 'gemini-2.0-flash-lite', 'gemini-1.5-pro', 'gemini-1.5-flash'],
  deepseek: ['deepseek-chat', 'deepseek-reasoner'],
}

export const KIND_DEFAULTS: Record<string, Omit<CreateProviderRequest, 'kind'>> = {
  ollama:    { display_name: 'Ollama (local)',     base_url: 'http://localhost:11434',                     model_id: '' },
  lmstudio:  { display_name: 'LM Studio (local)', base_url: 'http://localhost:1234',                      model_id: '' },
  openai:    { display_name: 'OpenAI',             base_url: 'https://api.openai.com',                    model_id: 'gpt-4o' },
  anthropic: { display_name: 'Anthropic',          base_url: 'https://api.anthropic.com',                 model_id: 'claude-3-5-sonnet-20241022' },
  gemini:    { display_name: 'Google Gemini',      base_url: 'https://generativelanguage.googleapis.com', model_id: 'gemini-2.0-flash' },
  deepseek:  { display_name: 'DeepSeek',           base_url: 'https://api.deepseek.com',                  model_id: 'deepseek-chat' },
  openrouter: { display_name: 'OpenRouter',        base_url: 'https://openrouter.ai/api/v1',              model_id: '' },
}
