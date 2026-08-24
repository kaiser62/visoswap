/** Unit tests for the informational project socket client (plan 05.1-05 Task 1).
 *
 * The backend contract (backend/api/ws.py): the socket is informational only —
 * if it drops, playback and generation continue and the frontend refetches the
 * frame index on reconnect rather than trusting replayed events. The hub also
 * drops the oldest event for a saturated subscriber, so missed events are
 * simply gone; the index is the only truth.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  openProjectSocket,
  RECONNECT_BASE_MS,
  RECONNECT_MAX_MS,
} from '../lib/ws'
import type { SocketEvent } from '../types'

/** Minimal stand-in installed over the global WebSocket for every test. */
class FakeSocket {
  static instances: FakeSocket[] = []
  url: string
  readyState = 0
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: unknown }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeSocket.instances.push(this)
  }

  // -- test-side drivers (the wire, from the server's point of view) --------
  serverOpen() {
    this.readyState = 1
    this.onopen?.()
  }
  serverMessage(data: unknown) {
    this.onmessage?.({ data })
  }
  serverClose() {
    this.readyState = 3
    this.onclose?.()
  }

  // -- browser-side API the client may call ---------------------------------
  close() {
    this.readyState = 3
  }
  send() {}
}

function last(): FakeSocket {
  return FakeSocket.instances[FakeSocket.instances.length - 1]
}

beforeEach(() => {
  vi.useFakeTimers()
  FakeSocket.instances = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('project socket client', () => {
  it('dispatches each parsed message to the handler and silently discards the heartbeat', () => {
    const events: SocketEvent[] = []
    const handle = openProjectSocket(
      'p1',
      (e) => events.push(e),
      () => {},
    )
    const sock = last()
    expect(sock.url).toContain('/ws/projects/p1')
    sock.serverOpen()
    sock.serverMessage(JSON.stringify({ type: 'ping' }))
    sock.serverMessage(
      JSON.stringify({
        type: 'generation_completed',
        timestamp: 4.5,
        path: '/api/projects/p1/frame/000004.500',
        duration: 0.1,
      }),
    )
    expect(events).toHaveLength(1)
    expect(events[0]).toMatchObject({ type: 'generation_completed', timestamp: 4.5 })
    handle.close()
  })

  it('discards unknown event types without changing state or throwing', () => {
    const events: SocketEvent[] = []
    const handle = openProjectSocket('p1', (e) => events.push(e), () => {})
    const sock = last()
    sock.serverOpen()
    expect(() =>
      sock.serverMessage(JSON.stringify({ type: 'mystery', payload: 1 })),
    ).not.toThrow()
    sock.serverMessage(JSON.stringify({ type: 'scheduler_started' }))
    expect(events.map((e) => e.type)).toEqual(['scheduler_started'])
    handle.close()
  })

  it('a malformed body neither throws out of the client nor stops the connection', () => {
    const events: SocketEvent[] = []
    const handle = openProjectSocket('p1', (e) => events.push(e), () => {})
    const sock = last()
    sock.serverOpen()
    expect(() => sock.serverMessage('not json {')).not.toThrow()
    expect(() => sock.serverMessage(42 as unknown as string)).not.toThrow()
    // The connection is still alive: a later valid event is dispatched.
    sock.serverMessage(JSON.stringify({ type: 'scheduler_stopped' }))
    expect(events).toHaveLength(1)
    handle.close()
  })

  it('reconnects after a drop with a bounded backoff and refetches on every successful open', () => {
    let opens = 0
    const handle = openProjectSocket('p1', () => {}, () => {
      opens += 1
    })
    // First open counts — refetch is invoked on EVERY successful open.
    last().serverOpen()
    expect(opens).toBe(1)

    // Drop one: reconnect waits the base delay before dialling again.
    last().serverClose()
    vi.advanceTimersByTime(RECONNECT_BASE_MS - 1)
    expect(FakeSocket.instances.length).toBe(1)
    vi.advanceTimersByTime(1)
    expect(FakeSocket.instances.length).toBe(2)
    last().serverOpen()
    expect(opens).toBe(2)

    // Backoff doubles per consecutive failure...
    last().serverClose()
    vi.advanceTimersByTime(RECONNECT_BASE_MS * 2 - 1)
    expect(FakeSocket.instances.length).toBe(2)
    vi.advanceTimersByTime(1)
    expect(FakeSocket.instances.length).toBe(3)
    last().serverClose()

    // ...and is bounded: after enough failures the wait never exceeds the cap.
    for (let i = 0; i < 8; i++) {
      last().serverClose()
      vi.advanceTimersByTime(RECONNECT_MAX_MS * 2)
    }
    const beforeCap = FakeSocket.instances.length
    last().serverClose()
    vi.advanceTimersByTime(RECONNECT_MAX_MS - 1)
    expect(FakeSocket.instances.length).toBe(beforeCap)
    vi.advanceTimersByTime(1)
    expect(FakeSocket.instances.length).toBe(beforeCap + 1)
    handle.close()
  })

  it('stops reconnecting once closed by the caller', () => {
    const handle = openProjectSocket('p1', () => {}, () => {})
    last().serverOpen()
    handle.close()
    // The browser still delivers the close event after close() — the client
    // must not dial again regardless.
    last().serverClose()
    vi.advanceTimersByTime(RECONNECT_MAX_MS * 10)
    expect(FakeSocket.instances.length).toBe(1)
  })

  it('refetches again on each subsequent reconnect open, not just the first', () => {
    const refetch = vi.fn()
    const handle = openProjectSocket('p1', () => {}, refetch)
    last().serverOpen()
    last().serverClose()
    vi.advanceTimersByTime(RECONNECT_BASE_MS)
    last().serverOpen()
    last().serverClose()
    vi.advanceTimersByTime(RECONNECT_BASE_MS * 2)
    last().serverOpen()
    expect(refetch).toHaveBeenCalledTimes(3)
    handle.close()
  })
})
