"""Phases of a flow, not actions in their own right.

`registry._discover` walks `photolib/actions/` with `pkgutil.iter_modules`,
which does not recurse. Living in this subpackage is therefore what keeps
these modules out of the registry — and so out of the sidebar — while
`sync_archives` goes on importing and running them directly.

Nothing here declares ID/TITLE/DESCRIPTION/ORDER. A module that wants to be
an action belongs one directory up.
"""
