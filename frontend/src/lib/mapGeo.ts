/** Shared geo math for the map: the click-to-search radius (also used by
 * InteractiveMap.tsx for its own click handler) and straight-line distance
 * for sorting "nearby" results. Kept in one place so the two stay in sync. */

// simplification: same fixed click-radius box as InteractiveMap.tsx (not
// the exact map viewport) — see that file's note for the tradeoff.
export const CLICK_RADIUS_LAT = 1.3
export const CLICK_RADIUS_LON = 2.5

export function bboxAroundPoint(lat: number, lon: number): [number, number, number, number] {
  return [lat - CLICK_RADIUS_LAT, lat + CLICK_RADIUS_LAT, lon - CLICK_RADIUS_LON, lon + CLICK_RADIUS_LON]
}

/** Haversine great-circle distance in km — good enough for sorting a
 * short "nearby" list, not for navigation-grade precision. */
export function distanceKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371
  const dLat = ((lat2 - lat1) * Math.PI) / 180
  const dLon = ((lon2 - lon1) * Math.PI) / 180
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(a))
}
