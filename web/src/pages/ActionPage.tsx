import type { ActionSpec } from '../api/types'

export function ActionPage({ actions }: { actions: ActionSpec[] }) {
  return <p>Actions ({actions.length}) — implemented in Task 15.</p>
}
