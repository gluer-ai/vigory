import {
  Boxes,
  Building2,
  Calendar,
  Car,
  FileText,
  Fingerprint,
  MapPin,
  User,
  Wrench,
  type LucideIcon,
} from 'lucide-react'

/** One icon + accent color per root entity class (DESIGN.md thesis). */
export const ENTITY_CLASS_META: Record<string, { icon: LucideIcon; colorVar: string }> = {
  PERSON: { icon: User, colorVar: '--color-class-person' },
  ORGANIZATION: { icon: Building2, colorVar: '--color-class-organization' },
  LOCATION: { icon: MapPin, colorVar: '--color-class-location' },
  FACILITY: { icon: Boxes, colorVar: '--color-class-facility' },
  VEHICLE: { icon: Car, colorVar: '--color-class-vehicle' },
  EQUIPMENT: { icon: Wrench, colorVar: '--color-class-equipment' },
  EVENT: { icon: Calendar, colorVar: '--color-class-event' },
  INFORMATION_OBJECT: { icon: FileText, colorVar: '--color-class-informationobject' },
  IDENTIFIER: { icon: Fingerprint, colorVar: '--color-class-identifier' },
}

export function classMeta(entityClass: string) {
  return ENTITY_CLASS_META[entityClass] ?? { icon: Boxes, colorVar: '--color-text-muted' }
}

/** Rolls a confidence code's letter (source reliability) into a 3-way scale. */
export function confidenceLevel(code: string): 'high' | 'medium' | 'low' {
  const letter = code?.[0]?.toUpperCase()
  if (letter === 'A' || letter === 'B') return 'high'
  if (letter === 'C' || letter === 'D') return 'medium'
  return 'low'
}
