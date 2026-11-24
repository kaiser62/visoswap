import { entriesAfter, type FrameIndex } from './frameindex'

/** Seconds of already-generated frames to keep warm ahead of the playhead.
 *
 * Over a tunnel one image costs a few hundred ms round trip, so a lookahead of a
 * few frames (about 125 ms at 24 fps) always stalls even when every frame is
 * already generated. A few seconds hides the latency. */
export const PREFETCH_SECONDS = 4
/** Parallel image loads. High enough to cover tunnel latency, low enough not to
 * starve generation traffic or the browser's per-host connection limit. */
export const PREFETCH_CONCURRENCY = 8
/** Decoded images kept referenced so the browser cannot drop them early. */
const MAX_KEPT = 160

export interface Prefetcher {
  warm(index: FrameIndex, t: number, fps: number): void
  reset(): void
}

export function createPrefetcher(): Prefetcher {
  const seen = new Set<string>()
  const kept: HTMLImageElement[] = []
  const queue: string[] = []
  let inflight = 0

  const pump = () => {
    while (inflight < PREFETCH_CONCURRENCY && queue.length > 0) {
      const url = queue.shift()!
      inflight += 1
      const img = new Image()
      const done = () => {
        inflight -= 1
        pump()
      }
      img.onload = done
      img.onerror = () => {
        seen.delete(url) // let a later pass retry a failed frame
        done()
      }
      img.src = url
      void img.decode?.().catch(() => {})
      kept.push(img)
      if (kept.length > MAX_KEPT) kept.shift()
    }
  }

  return {
    warm(index, t, fps) {
      const count = Math.max(3, Math.ceil(PREFETCH_SECONDS * (fps > 0 ? fps : 24)))
      // The queue is re-planned each call: the playhead may have jumped.
      queue.length = 0
      for (const entry of entriesAfter(index, t, count)) {
        if (seen.has(entry.url)) continue
        seen.add(entry.url)
        queue.push(entry.url)
      }
      pump()
    },
    reset() {
      seen.clear()
      queue.length = 0
      kept.length = 0
    },
  }
}
