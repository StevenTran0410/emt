export interface NativeFunction {
  name: string
  available: boolean
  description: string
  module: string
}

export interface Diagnostics {
  python_version: string
  native_module_loaded: boolean
  native_functions: NativeFunction[]
}

export interface ClassifierStatus {
  trained: boolean
  backend: string
  builtin_examples: number
  user_examples: number
}

export interface ClassifierExample {
  id: string
  text: string
  is_deep_research: boolean
  created_at: string
}
