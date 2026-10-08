import type { ClassDef } from './types'

export interface TreeNode {
  cls: ClassDef
  children: TreeNode[]
  /** Entities on this exact class. */
  own: number
  /** Entities on this class and everything below it. */
  total: number
}

/** Build the class forest from the flat list (parent_key links), sorted by
 * key so output is deterministic. Classes whose parent is missing become roots
 * rather than disappearing. */
export function buildTree(classes: ClassDef[], counts: Record<string, number>): TreeNode[] {
  const nodes = new Map<string, TreeNode>()
  for (const cls of classes) {
    const own = counts[cls.key] ?? 0
    nodes.set(cls.key, { cls, children: [], own, total: own })
  }
  const roots: TreeNode[] = []
  for (const node of nodes.values()) {
    const parent = node.cls.parent_key ? nodes.get(node.cls.parent_key) : undefined
    if (parent) parent.children.push(node)
    else roots.push(node)
  }
  const sort = (list: TreeNode[]): number => {
    list.sort((a, b) => a.cls.key.localeCompare(b.cls.key))
    let sum = 0
    for (const n of list) {
      n.total = n.own + sort(n.children)
      sum += n.total
    }
    return sum
  }
  sort(roots)
  return roots
}

/** Keep nodes matching `match`, plus their ancestors (so a hit stays reachable)
 * and their whole subtree is dropped unless it also matches. */
export function filterTree(nodes: TreeNode[], match: (cls: ClassDef) => boolean): TreeNode[] {
  const out: TreeNode[] = []
  for (const node of nodes) {
    const children = filterTree(node.children, match)
    if (match(node.cls) || children.length > 0) out.push({ ...node, children })
  }
  return out
}

/** Keys of every node on the path above `key` (for auto-expanding a search). */
export function ancestorKeys(key: string): string[] {
  const parts = key.split('.')
  return parts.slice(1).map((_, i) => parts.slice(0, i + 1).join('.'))
}

/** Turn a typed name into a taxonomy segment: "Law firm" -> "LAW_FIRM".
 * Returns null when nothing valid remains or it would contain a dot. */
export function toKeySegment(name: string): string | null {
  const seg = name
    .trim()
    .toUpperCase()
    .replace(/[\s-]+/g, '_')
  return /^[A-Z][A-Z0-9_]{0,49}$/.test(seg) ? seg : null
}
