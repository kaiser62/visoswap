/** Studio card frame and two-column grid (plan 05.1-04 Task 3).
 *
 * Ported from the user-approved webui2 reference (D:/Visomaster/webui2):
 * hairline-bordered cards with raised collapsible headers, laid out on the
 * `.layout` two-column grid that collapses below the reference breakpoint.
 *
 * D-02 lives here: StudioCard ALWAYS mounts its body and hides it with a CSS
 * utility when closed. Conditionally rendering children would silently drop
 * the Controls card's 201 gated schema controls from the DOM the moment a
 * user collapses the card, breaking the `[data-key]` count gate.
 */

import { useState, type ReactNode } from 'react'

export function StudioCard({
  title,
  right,
  defaultOpen = false,
  children,
}: {
  title: string
  right?: ReactNode
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className="overflow-hidden rounded-xl border border-line bg-card">
      <div
        className="flex min-h-12 w-full cursor-pointer items-center justify-between gap-3 border-b border-line bg-raised px-4 py-2.5 text-left"
        onClick={() => setOpen((o) => !o)}
      >
        <h2 className="text-[15px] font-semibold text-text">{title}</h2>
        <span className="ml-auto flex items-center gap-3">
          {right}
          <button
            type="button"
            aria-expanded={open}
            className="text-xs font-semibold text-accent"
          >
            {open ? 'Collapse' : 'Expand'}
          </button>
        </span>
      </div>
      {/* Always in the tree; closing is display:none, never an unmount. */}
      <div className={open ? '' : 'hidden'}>{children}</div>
    </section>
  )
}

export function StudioGrid({
  left,
  right,
}: {
  left: ReactNode
  right: ReactNode
}) {
  return (
    <div className="mx-auto grid w-full max-w-[1550px] grid-cols-1 gap-3.5 p-3.5 min-[970px]:grid-cols-[minmax(420px,1.2fr)_minmax(310px,0.82fr)]">
      <div className="flex min-w-0 flex-col gap-3.5">{left}</div>
      <div className="flex min-w-0 flex-col gap-3.5">{right}</div>
    </div>
  )
}
