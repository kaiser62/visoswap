/** Small hand-rolled Tailwind primitives (D-07) per the approved UI-SPEC tokens. */

import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from 'react'

const ACCENT = '#2563EB'
const DESTRUCTIVE = '#DC2626'

export function Button({
  variant = 'primary',
  className = '',
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'destructive' | 'ghost'
}) {
  const base =
    'inline-flex h-8 items-center justify-center gap-1 rounded px-3 text-sm font-semibold ' +
    'transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 ' +
    'disabled:pointer-events-none disabled:opacity-50'
  const variants = {
    primary: `bg-blue-600 text-white hover:bg-blue-700`,
    destructive: `bg-red-600 text-white hover:bg-red-700`,
    ghost: `bg-transparent text-neutral-600 hover:bg-neutral-100`,
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
      className={`rounded-lg border border-neutral-200 bg-white p-4 ${className}`}
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
      className={`inline-flex h-5 items-center rounded-full bg-blue-600 px-2 text-xs font-semibold text-white ${className}`}
    >
      {children}
    </span>
  )
}

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex h-8 items-center gap-2 text-sm text-neutral-500">
      <span
        className="h-4 w-4 animate-spin rounded-full border-2 border-neutral-200 border-t-blue-600"
        aria-hidden
      />
      <span>{label}</span>
    </div>
  )
}

export function Skeleton({ className = '' }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`h-8 animate-pulse rounded bg-neutral-100 ${className}`}
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
        className="w-full max-w-md rounded-lg border border-neutral-200 bg-white p-6"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <h2 className="mb-4 text-base font-semibold text-neutral-900">{title}</h2>
        {children}
      </div>
    </div>
  )
}

// Re-export color constants for inline styles (accent/destructive SVG etc.).
export { ACCENT, DESTRUCTIVE }
