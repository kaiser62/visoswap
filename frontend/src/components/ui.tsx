/** Small hand-rolled Tailwind primitives (D-07), restyled to the Studio
 * token vocabulary (plan 05.1-04 Task 1). Every colour reads from the
 * `--color-*` tokens defined in index.css — no hardcoded surfaces.
 */

import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from 'react'

const ACCENT = '#32bbd0'
const DESTRUCTIVE = '#ef7265'

export function Button({
  variant = 'primary',
  className = '',
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'destructive' | 'ghost'
}) {
  const base =
    'inline-flex h-8 items-center justify-center gap-1 rounded px-3 text-sm font-semibold ' +
    'transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent ' +
    'disabled:pointer-events-none disabled:opacity-50'
  const variants = {
    primary: `bg-accent text-bg hover:bg-accent/90`,
    destructive: `bg-bad text-bg hover:bg-bad/90`,
    ghost: `bg-transparent text-muted hover:bg-raised`,
  }
  return (
    <button
      className={`${base} ${variants[variant]} ${className}`}
      {...rest}
    />
  )
}

export function Card({
  className = '',
  ...rest
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`rounded-lg border border-line bg-card p-4 ${className}`}
      {...rest}
    />
  )
}

export function Badge({
  className = '',
  children,
}: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={`inline-flex h-5 items-center rounded-full bg-accent px-2 text-xs font-semibold text-bg ${className}`}
    >
      {children}
    </span>
  )
}

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex h-8 items-center gap-2 text-sm text-muted">
      <span
        className="h-4 w-4 animate-spin rounded-full border-2 border-line border-t-accent"
        aria-hidden
      />
      <span>{label}</span>
    </div>
  )
}

export function Skeleton({ className = '' }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`h-8 animate-pulse rounded bg-raised ${className}`}
      aria-hidden
    />
  )
}

export function Modal({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
}) {
  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg border border-line bg-card p-6"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <h2 className="mb-4 text-base font-semibold text-text">{title}</h2>
        {children}
      </div>
    </div>
  )
}

// Re-export color constants for inline styles (accent/destructive SVG etc.).
export { ACCENT, DESTRUCTIVE }
