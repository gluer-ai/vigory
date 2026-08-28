import { Menu, X } from 'lucide-react'
import { useState, type ReactNode } from 'react'

interface AppShellProps {
  leftRail: ReactNode
  canvas: ReactNode
  drawer?: ReactNode
}

/** Three-pane app shell: left rail (schema/scope controls), canvas, inspector
 * drawer slot. Below lg the rail becomes an off-canvas overlay (toggled by a
 * menu button) so the canvas keeps a usable width down to 320px (WCAG 1.4.10). */
export function AppShell({ leftRail, canvas, drawer }: AppShellProps) {
  const [railOpen, setRailOpen] = useState(false)

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[var(--color-surface-0)] text-[var(--color-text-primary)]">
      <a
        href="#canvas-main"
        className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:m-2 focus:rounded focus:bg-[var(--color-focus)] focus:px-3 focus:py-2 focus:text-[var(--color-text-inverse)]"
      >
        Skip to graph content
      </a>

      {railOpen && (
        <button
          type="button"
          aria-label="Dismiss scope controls overlay"
          className="fixed inset-0 z-30 bg-black/50 lg:hidden"
          onClick={() => setRailOpen(false)}
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-[280px] shrink-0 -translate-x-full flex-col gap-6 overflow-y-auto border-r border-[var(--color-border)] bg-[var(--color-surface-1)] p-4 transition-transform duration-[var(--duration-base)] ease-[var(--ease-standard)] lg:static lg:translate-x-0 ${
          railOpen ? 'translate-x-0' : ''
        }`}
        aria-label="Schema and scope controls"
      >
        <button
          type="button"
          onClick={() => setRailOpen(false)}
          aria-label="Close menu"
          className="self-end rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] lg:hidden"
        >
          <X size={18} />
        </button>
        {leftRail}
      </aside>

      <main id="canvas-main" className="relative flex-1 overflow-hidden">
        <button
          type="button"
          onClick={() => setRailOpen(true)}
          aria-label="Open scope controls"
          className="absolute left-3 top-3 z-20 rounded-md border border-[var(--color-border)] bg-[var(--color-surface-1)] p-2 text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)] lg:hidden"
        >
          <Menu size={18} />
        </button>
        {canvas}
      </main>
      {drawer}
    </div>
  )
}
