import { NavLink } from 'react-router-dom'
import type { ActionSpec } from '../api/types'

export function Nav({ actions }: { actions: ActionSpec[] }) {
  const flows = actions.filter((a) => a.group === 'flow')
  // `!== 'flow'` rather than `=== 'tool'` on purpose: sync_tags still
  // defaults to "advanced" until Plan B folds it, and an exact match would
  // drop it out of the sidebar while it is still the only way to push tags
  // to Drive.
  const tools = actions.filter((a) => a.group !== 'flow')

  return (
    <nav className="nav">
      <h1>Photo Library</h1>
      <section>
        <h2>Setup</h2>
        <NavLink to="/settings">Settings</NavLink>
      </section>
      <section>
        <h2>Flows</h2>
        {flows.map((action) => (
          <NavLink key={action.id} to={`/actions/${action.id}`}>
            {action.title}
          </NavLink>
        ))}
      </section>
      <section>
        <h2>Browse</h2>
        <NavLink to="/library">Library</NavLink>
        <NavLink to="/tags">Tags</NavLink>
      </section>
      <section>
        <h2>Activity</h2>
        <NavLink to="/review">Review Plan</NavLink>
        <NavLink to="/jobs">Jobs</NavLink>
      </section>
      <section>
        <h2>Tools</h2>
        {tools.map((action) => (
          <NavLink key={action.id} to={`/actions/${action.id}`}>
            {action.title}
          </NavLink>
        ))}
      </section>
    </nav>
  )
}
