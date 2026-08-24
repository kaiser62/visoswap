/** The frame index: a plain sorted array of completed frames (plan 05.1-05).
 *
 * This module is the overlay's hot path — `frameAtOrBefore` runs on every
 * video timeupdate — so it is deliberately free of React and of any I/O: a
 * pure data structure over the frames response, testable without rendering
 * anything. The nearest-*previous* policy (D-15d) lives here: the lookup
 * returns null rather than a future frame, so the swapped image can lag but
 * can never lead.
 */

import type { FrameEntry, FramesIndexResponse } from '../types'

/** One completed generated frame the overlay can display. */
export interface FrameIndexEntry {
  timestamp: number
  url: string
}

export type FrameIndex = FrameIndexEntry[]

/** Build the index from a frames response: completed entries only, ascending. */
export function buildIndex(response: FramesIndexResponse): FrameIndex {
  const entries: FrameIndexEntry[] = []
  for (const frame of response.frames as FrameEntry[]) {
    if (frame.status === 'completed' && typeof frame.url === 'string') {
      entries.push({ timestamp: frame.timestamp, url: frame.url })
    }
  }
  entries.sort((a, b) => a.timestamp - b.timestamp)
  return entries
}

/**
 * Greatest entry whose timestamp is <= t, or null when there is none.
 * Binary search on the sorted index; never scans, never looks forward.
 */
export function frameAtOrBefore(index: FrameIndex, t: number): FrameIndexEntry | null {
  let lo = 0
  let hi = index.length - 1
  let best = -1
  while (lo <= hi) {
    const mid = lo + ((hi - lo) >> 1)
    if (index[mid].timestamp <= t) {
      best = mid
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  return best === -1 ? null : index[best]
}

/**
 * Insert a newly completed frame in sorted position, or replace an existing
 * entry at the same timestamp. Returns a new array; the input is untouched.
 */
export function upsert(index: FrameIndex, entry: FrameIndexEntry): FrameIndex {
  const next = [...index]
  let lo = 0
  let hi = next.length
  while (lo < hi) {
    const mid = lo + ((hi - lo) >> 1)
    if (next[mid].timestamp < entry.timestamp) lo = mid + 1
    else hi = mid
  }
  if (lo < next.length && next[lo].timestamp === entry.timestamp) {
    next[lo] = { ...entry }
  } else {
    next.splice(lo, 0, { ...entry })
  }
  return next
}
