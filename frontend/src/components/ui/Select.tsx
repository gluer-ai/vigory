import * as RadixSelect from '@radix-ui/react-select'
import { Check, ChevronDown } from 'lucide-react'

interface SelectProps {
  value: string
  onValueChange: (value: string) => void
  options: { value: string; label: string }[]
  placeholder?: string
  'aria-label'?: string
}

export function Select({ value, onValueChange, options, placeholder, ...aria }: SelectProps) {
  return (
    <RadixSelect.Root value={value} onValueChange={onValueChange}>
      <RadixSelect.Trigger
        className="inline-flex w-full items-center justify-between gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface-1)] py-1.5 ps-3 pe-2 text-sm text-[var(--color-text-primary)] hover:border-[var(--color-border-strong)]"
        aria-label={aria['aria-label']}
      >
        <RadixSelect.Value placeholder={placeholder} />
        <RadixSelect.Icon>
          <ChevronDown size={14} className="text-[var(--color-text-muted)]" />
        </RadixSelect.Icon>
      </RadixSelect.Trigger>
      <RadixSelect.Portal>
        <RadixSelect.Content className="z-50 overflow-hidden rounded-md border border-[var(--color-border)] bg-[var(--color-surface-1)] shadow-lg">
          <RadixSelect.Viewport className="max-h-72 p-1">
            {options.map((opt) => (
              <RadixSelect.Item
                key={opt.value}
                value={opt.value}
                className="flex cursor-pointer items-center justify-between rounded px-2 py-1.5 text-sm text-[var(--color-text-primary)] outline-none data-[highlighted]:bg-[var(--color-surface-hover)]"
              >
                <RadixSelect.ItemText>{opt.label}</RadixSelect.ItemText>
                <RadixSelect.ItemIndicator>
                  <Check size={14} />
                </RadixSelect.ItemIndicator>
              </RadixSelect.Item>
            ))}
          </RadixSelect.Viewport>
        </RadixSelect.Content>
      </RadixSelect.Portal>
    </RadixSelect.Root>
  )
}
