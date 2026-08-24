/** The informational project socket client (plan 05.1-05).
 *
 * Protocol: backend/api/ws.py — hello on open, ping every 20s, JSON events
 * after that. The socket is informational only: if it drops, playback and
 * generation continue. Two consequences are enforced here:
 *
 * 1. Reconnect uses a bounded exponential backoff so a flapping socket cannot
 *    spin, and every successful open (including the first) invokes the caller's
 *    refetch callback — the hub drops the oldest event for a saturated
 *    subscriber, so anything missed while disconnected is simply gone and the
 *    refetched frame index is the only truth.
 * 2. Every frame is parsed inside a guard and dispatched only when its `type`
 *    matches the event union; an unparseable or unknown frame is discarded
 *    without throwing and without changing state (T-05.1-05-01).
 */

import type { SocketEvent } from '../types'

/** First reconnect delay; doubles per consecutive failure. */
export const RECONNECT_BASE_MS = 500
/** Backoff ceiling — a flapping socket waits at most this long between dials. */
export const RECONNECT_MAX_MS = 8_000

export interface SocketHandle {
  /** Stop the client permanently: closes the socket and cancels reconnects. */
  close: () => void
}

function socketUrl(projectId: string): string {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${window.location.host}/ws/projects/${projectId}`
}

function parseEvent(raw: unknown): SocketEvent | null {
  if (typeof raw !== 'string') return null
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null // malformed body: ignore, keep the connection
  }
  if (typeof parsed !== 'object' || parsed === null) return null
  const candidate = parsed as { type?: unknown }
  switch (candidate.type) {
    case 'hello':
    case 'ping':
    case 'scheduler_started':
    case 'scheduler_stopped':
    case 'seek_reprioritized':
    case 'queue_updated':
    case 'generation_started':
    case 'generation_completed':
    case 'generation_failed':
      return parsed as SocketEvent
    default:
      return null // unknown type: discard rather than guess at its shape
  }
}

/**
 * Open the project socket. Returns a close handle; the client reconnects with
 * bounded backoff after any unexpected close and stops for good once closed by
 * the caller.
 */
export function openProjectSocket(
  projectId: string,
  onEvent: (event: SocketEvent) => void,
  refetch: () => void,
): SocketHandle {
  let stopped = false
  let attempt = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  let socket: WebSocket | null = null

  const connect = () => {
    if (stopped) return
    try {
      socket = new WebSocket(socketUrl(projectId))
    } catch {
      scheduleReconnect()
      return
    }
    socket.onopen = () => {
      // Refetch on EVERY open — replayed events cannot be trusted because the
      // backend may have dropped them while we were gone.
      refetch()
    }
    socket.onmessage = (ev: MessageEvent) => {
      const event = parseEvent(ev.data)
      if (event !== null && event.type !== 'ping') onEvent(event)
      // Backoff resets only on proven liveness (any received frame), not on
      // open: a socket that flaps open→drop never gets to zero its attempt
      // counter, so the bounded ceiling keeps it from spinning (T-05.1-05-03).
      attempt = 0
    }
    socket.onclose = () => {
      if (stopped) return
      scheduleReconnect()
    }
    socket.onerror = () => {
      /* the close event follows; backoff handles the retry cadence */
    }
  }

  const scheduleReconnect = () => {
    if (stopped) return
    const delay = Math.min(
      RECONNECT_BASE_MS * 2 ** attempt,
      RECONNECT_MAX_MS,
    )
    attempt += 1
    timer = setTimeout(connect, delay)
  }

  connect()

  return {
    close: () => {
      stopped = true
      if (timer !== null) clearTimeout(timer)
      timer = null
      try {
        socket?.close()
      } catch {
        /* already closing or never opened */
      }
    },
  }
}
