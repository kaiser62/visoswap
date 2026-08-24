/** Unit tests for the frame index (plan 05.1-05 Task 1).
 *
 * The index is the overlay's hot path: called on every timeupdate, so it must
 * be a plain sorted array with a binary-search nearest-previous lookup that
 * never returns a future frame (D-06/D-15d) and never needs React.
 */

import { describe, expect, it } from 'vitest'
import { buildIndex, frameAtOrBefore, upsert } from '../lib/frameindex'
import type { FrameEntry, FramesIndexResponse } from '../types'

function entry(timestamp: number, url: string | null, status = 'completed'): FrameEntry {
  return {
    timestamp,
    status: status as FrameEntry['status'],
    priority: 1,
    duration: null,
    attempts: 0,
    error: null,
    url,
  }
}

function resp(frames: FrameEntry[]): FramesIndexResponse {
  return { project_id: 'p1', interval: 1 / 24, duration: 120, frames }
}

describe('buildIndex', () => {
  it('keeps only completed entries, sorted ascending by timestamp, each carrying its url', () => {
    const index = buildIndex(
      resp([
        entry(3.0, '/api/projects/p1/frame/000003.000'),
        entry(1.0, null, 'pending'),
        entry(2.0, '/api/projects/p1/frame/000002.000'),
        entry(1.5, null, 'failed'),
        entry(0.5, '/api/projects/p1/frame/000000.500'),
      ]),
    )
    expect(index.map((e) => e.timestamp)).toEqual([0.5, 2.0, 3.0])
    expect(index.every((e) => typeof e.url === 'string')).toBe(true)
    expect(index[0].url).toBe('/api/projects/p1/frame/000000.500')
  })
})

describe('frameAtOrBefore', () => {
  it('returns the entry with the greatest timestamp less than or equal to t', () => {
    const index = buildIndex(resp([entry(1, 'u1'), entry(2, 'u2'), entry(3, 'u3')]))
    expect(frameAtOrBefore(index, 2.5)?.timestamp).toBe(2)
    // Boundary is inclusive: exactly on a frame means that frame.
    expect(frameAtOrBefore(index, 3)?.timestamp).toBe(3)
    expect(frameAtOrBefore(index, 0.999)?.timestamp).toBe(1)
  })

  it('returns null when every entry is in the future, and null on an empty index', () => {
    const future = buildIndex(resp([entry(10, 'u10'), entry(20, 'u20')]))
    expect(frameAtOrBefore(future, 9.9)).toBeNull()
    expect(frameAtOrBefore([], 5)).toBeNull()
  })

  it('is a binary search: correct on ten thousand entries, not a scan with a wrong branch', () => {
    const index = Array.from({ length: 10_000 }, (_, i) =>
      entry(i, `u${i}`),
    )
    expect(frameAtOrBefore(index, 5000.5)?.timestamp).toBe(5000)
    expect(frameAtOrBefore(index, 0)?.timestamp).toBe(0)
    expect(frameAtOrBefore(index, 9999.99)?.timestamp).toBe(9999)
    expect(frameAtOrBefore(index, 1234 + 0.5)?.timestamp).toBe(1234)
    // A descending-mid bug or off-by-one hi bound breaks at least one of these.
    expect(frameAtOrBefore(index, 8675.309)?.timestamp).toBe(8675)
    expect(frameAtOrBefore(index, -1)).toBeNull()
  })
})

describe('upsert', () => {
  it('inserts a newly completed frame in sorted position and replaces an existing entry at the same timestamp', () => {
    const base = buildIndex(resp([entry(1, 'u1'), entry(3, 'u3')]))
    const withTwo = upsert(base, { timestamp: 2, url: 'u2' })
    expect(withTwo.map((e) => e.timestamp)).toEqual([1, 2, 3])
    // Same-timestamp upsert replaces rather than duplicating.
    const replaced = upsert(withTwo, { timestamp: 2, url: 'u2-better' })
    expect(replaced.map((e) => e.timestamp)).toEqual([1, 2, 3])
    expect(replaced[1].url).toBe('u2-better')
    // The input array is left untouched (state containers rely on this).
    expect(base.map((e) => e.timestamp)).toEqual([1, 3])
  })
})
