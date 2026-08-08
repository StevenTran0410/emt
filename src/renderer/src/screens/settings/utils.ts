export function backendLabel(b: string): { text: string; color: string } {
  if (b === 'sklearn') return { text: 'sklearn (TF-IDF + LR)', color: 'text-green-400 bg-green-950 border-green-800' }
  if (b === 'pure_python') return { text: 'Pure Python (Naïve Bayes)', color: 'text-yellow-400 bg-yellow-950 border-yellow-800' }
  return { text: 'Not trained', color: 'text-gray-400 bg-surface-raised border-surface-border' }
}
