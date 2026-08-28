import type { ButtonHTMLAttributes } from 'react'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'ghost'
}

export function Button({ variant = 'ghost', className = '', ...props }: ButtonProps) {
  const base =
    'inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors duration-[var(--duration-fast)] ease-[var(--ease-standard)] disabled:opacity-50 disabled:pointer-events-none'
  const variants = {
    primary: 'bg-[var(--color-focus)] text-[var(--color-text-inverse)] hover:brightness-110',
    ghost:
      'text-[var(--color-text-primary)] border border-[var(--color-border)] hover:bg-[var(--color-surface-hover)] hover:border-[var(--color-border-strong)]',
  }
  return <button className={`${base} ${variants[variant]} ${className}`} {...props} />
}
