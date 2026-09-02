import {
  Boxes,
  Building2,
  Calendar,
  Car,
  FileText,
  Fingerprint,
  MapPin,
  Plane,
  Ship,
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

/** Icon overrides by VEHICLE.* subclass branch (ontology prefix, not exact
 * leaf) — same accent color as the root VEHICLE class, but a shape that
 * reads instantly (plane vs. ship vs. car) instead of one generic car icon
 * for every vehicle. Matches the whole AIR_VEHICLE/SEA_VEHICLE branch so
 * any leaf (fighter, airliner, tanker, frigate, ...) gets the right icon. */
const VEHICLE_SUBCLASS_ICON_PREFIX: [prefix: string, icon: LucideIcon][] = [
  ['VEHICLE.AIR_VEHICLE.', Plane],
  ['VEHICLE.SEA_VEHICLE.', Ship],
]

export function classMeta(entityClass: string, entitySubclass?: string) {
  const base = ENTITY_CLASS_META[entityClass] ?? { icon: Boxes, colorVar: '--color-text-muted' }
  const override = entitySubclass
    ? VEHICLE_SUBCLASS_ICON_PREFIX.find(([prefix]) => entitySubclass.startsWith(prefix))
    : undefined
  return override ? { ...base, icon: override[1] } : base
}

/** Rolls a confidence code's letter (source reliability) into a 3-way scale. */
export function confidenceLevel(code: string): 'high' | 'medium' | 'low' {
  const letter = code?.[0]?.toUpperCase()
  if (letter === 'A' || letter === 'B') return 'high'
  if (letter === 'C' || letter === 'D') return 'medium'
  return 'low'
}
