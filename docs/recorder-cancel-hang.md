# The cancelled-recording hang

Roadmap criterion 1 requires
`test_recorder.py::test_a_cancelled_run_leaves_a_playable_partial` to complete
and pass — fixed, not skipped or deselected. On the predecessor it hung
indefinitely. This document is the only place the diagnosis survives: it records
the mechanism, the measured variants, why only one of the fourteen recorder
tests hangs, and what Phase 6's live cancel must re-verify.

## The one-sentence mechanism

Killing a subprocess whose output pipe is **paused** (flow control suspended
because nobody is reading) and then awaiting it in the same event-loop step
never returns — even once the child is dead and its returncode is set — because
asyncio's exit waiter is woken only when every pipe has disconnected, and a
paused pipe has no read outstanding, so EOF is never observed.

## The five-step mechanism

1. The test holds the frontier at `t = 3.0` with `grace = 0.0`. The pump writes
   through `t = 3.0` and then parks in `_await_deadline` (because
   `3.1 + 0.0 > 3.0`). From that instant nothing reads the decoder's stdout
   again.
2. The decoder keeps producing. Raw `bgr24` at 160x120 is 57,600 bytes/frame
   against a 64 KiB `StreamReader` limit, so the buffer passes the high-water
   mark almost immediately and flow control **pauses** the stdout transport.
3. `aclose()` cancels the pump, then calls `self._decoder.kill()` and, in the
   same event-loop step, `await self._decoder.wait()`. At that instant the
   child's exit has not been observed, so `BaseSubprocessTransport._wait()`
   appends a waiter and suspends.
4. The child dies. `_process_exited` fires and sets the returncode — measured
   `proc.returncode=1`, `transport._returncode=1`. **The process is dead and the
   wait still does not return.**
5. The appended waiter is only ever woken from `_call_connection_lost`, which
   `_try_finish` reaches only when
   `all(p.disconnected for p in self._pipes.values())`. The paused stdout pipe
   has no read outstanding, so EOF is never observed, `pipe_connection_lost`
   never fires, `disconnected` stays `False`, and the waiter is stranded for the
   life of the process. There is no timeout on that await.

## The counter-intuitive fact

The process is **already dead with its returncode set** while the wait is still
pending. This is what makes it a stdlib-shape bug rather than an ffmpeg one, and
it is the fact most likely to be doubted later.

## The eight measured variants

Against a real killed `ffmpeg` whose stdout transport was asserted paused first:

| Variant | Behaviour | Time |
|---------|-----------|------|
| A today: `kill(); await wait()` | HANG | 12s timeout, rc=1 |
| B `kill(); await sleep(0); await wait()` | HANG | 12s timeout, rc=1 |
| C `kill(); await sleep(0.05); await wait()` | OK | 0.06s (rc=1) |
| D `kill(); stdout transport.close(); wait()` | OK | 0.00s (rc=1) |
| E `kill(); proc transport.close(); wait()` | OK | 0.01s (rc=1) |
| F `kill(); resume+drain to EOF; wait()` | OK | 0.01s (rc=1) |
| G `kill(); wait_for(wait(), 3)` | OK | 3.01s (rc=1) |
| H `kill(); while await proc.stdout.read(65536): pass; await proc.wait()` | OK | 0.01s, drained=3398400 (rc=1) |

Read A against C: the difference is 50 ms of unrelated sleep. `kill()` is
synchronous and `wait()` suspends immediately, so the loop never gets a chance
to poll the process handle between them — the current shape loses the race
every time rather than intermittently.

Read B against C: a single loop yield is not enough.

Read G against the roadmap: a bounded wait *passes the test* while leaving the
defect in place and costing the timeout on every cancel. It is the answer the
roadmap explicitly rules out. It is **rejected** here; the mechanism test in
`tests/test_subprocess_wait_shape.py` fails against it, so it cannot be
reintroduced as a fix without the suite saying so.

D and E reach into `proc.stdout._transport` and `proc._transport`, both private.
H uses only the public `StreamReader.read()` — the reader's own
`_maybe_resume_transport` resumes the transport on its own once the buffer
falls below half the limit, so `resume_reading()` need not be called by hand.
**H is the fix.**

## Why only one of the fourteen recorder tests hangs

Every other test that starts a pump sets the frontier to `1e9`, so
`_await_deadline` never blocks and `_pump_frames` runs until
`stdout.readexactly` raises `IncompleteReadError` at clean end of stream —
which means the pipe has been read to EOF and `disconnected` is already `True`
before `aclose()` kills anything. The cancel path is the only path that stops
the reader mid-stream.

## Why the 28-byte partial was a consequence, not a second bug

Measured mid-hang the partial is 28 bytes — an `ftyp` box and nothing else —
because the encoder's stdin is closed at `recorder.py:289-292`, *after* the
decoder wait that never returns, so nothing is ever flushed. The
`+frag_keyframe+empty_moov+default_base_moof` design was working correctly all
along; the file was empty because the close path never got far enough to close
a pipe.

With the drain applied, the cancelled-run test passes in **0.40 s** including its
`_decodes(partial)` assertion.

## The fix

In `aclose()`, after killing the decoder, drain its stdout to end of stream with
the public `StreamReader.read()`, and bound both the drain and the wait with the
existing `READ_TIMEOUT` (120 s). Reuse the encoder path's 30-second wait bound
shape so the two halves of `aclose()` read as one idea. Swallow
`BrokenPipeError`/`ConnectionResetError` on the drain, as the encoder path
already does. Move the drain outside the `returncode is None` guard so a decoder
that exited on its own still has its pipe drained. Use no private transport
attributes.

## For Phase 6

Roadmap criterion 2 is a **live** cancel mid-recording leaving a playable
partial — a different claim from the unit test's. The unit test cancels a
`Recorder`; the live check cancels a recording through the running application,
with the scheduler and a real generation load in play, then verifies:

- the output (or `.part`) file is playable end to end (`ffmpeg -v error -i … -f
  null -` exits 0);
- the backend process stays responsive after the cancel (no wedged task, no
  leaked `ffmpeg` handle);
- the partial is visibly non-trivial (far more than the 28-byte `ftyp` box that
  was the symptom of the hang).

That is what re-verifying means here — not re-deriving the mechanism, but
confirming the fix holds under a live generation load.
