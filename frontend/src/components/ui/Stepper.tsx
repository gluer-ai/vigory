import { Minus, Plus } from 'lucide-react'

interface StepperProps {
  value: number
  onChange: (value: number) => void
  min?: number
  max?: number
  label: string
}

export function Stepper({ value, onChange, min = 1, max = 5, label }: StepperProps) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-[var(--color-text-muted)]">{label}</span>
      <div
        className="flex items-center gap-1 rounded-md border border-[var(--color-border)]"
        role="group"
        aria-label={label}
      >
        <button
          type="button"
          className="p-1.5 text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-40"
          onClick={() => onChange(Math.max(min, value - 1))}
          disabled={value <= min}
          aria-label={`decrease ${label}`}
        >
          <Minus size={14} />
        </button>
        <span className="w-6 text-center font-mono text-sm" aria-live="polite">
          {value}
        </span>
        <button
          type="button"
          className="p-1.5 text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-40"
          onClick={() => onChange(Math.min(max, value + 1))}
          disabled={value >= max}
          aria-label={`increase ${label}`}
        >
          <Plus size={14} />
        </button>
      </div>
    </div>
  )
}
