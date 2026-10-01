import type { AgentOp } from './types'

/** Runs async tasks one at a time, in call order, even if an earlier one fails.
 * The voice model can fire several edit_sandbox calls back to back; each must
 * see the canvas the previous one produced. */
export function createSerialQueue() {
  let tail: Promise<unknown> = Promise.resolve()
  return function enqueue<T>(task: () => Promise<T>): Promise<T> {
    const run = tail.then(task)
    tail = run.catch(() => undefined)
    return run
  }
}

export interface SandboxToolDeps {
  describe: () => Promise<unknown>
  edit: (ops: AgentOp[]) => Promise<unknown>
}

/** Executes the sandbox tools the voice model may call. Returns `undefined`
 * for any other tool name so the caller can fall through to the backend's
 * knowledge-base tools. */
export async function handleSandboxTool(
  name: string,
  args: Record<string, unknown>,
  deps: SandboxToolDeps,
): Promise<unknown | undefined> {
  if (name === 'get_sandbox') return deps.describe()
  if (name === 'edit_sandbox') {
    const raw = args.ops
    const ops = Array.isArray(raw)
      ? raw.filter((o): o is AgentOp => !!o && typeof o === 'object' && typeof (o as AgentOp).op === 'string')
      : []
    if (ops.length === 0) return { error: 'No valid operations were given.' }
    return deps.edit(ops)
  }
  return undefined
}
