# Frame pack format

One HTTP response carrying many frames. Purpose: latency. A tunnelled backend
costs ~330 ms per round trip, so fetching twelve frames one at a time is twelve
round trips; a pack is one.

```
POST /api/projects/{project_id}/frames/pack
X-VisoSwap-Key: <access key>
Content-Type: application/json

{"keys": ["000051.633", "000051.675"], "w": 720, "q": 70}
```

`keys` are the canonical `000123.450` strings from `/api/projects/{id}/frames`
(the `url` field's last path segment, without the `?v=`). Up to 64, unique.
`w` and `q` are optional and apply to every frame in the pack; omit both to get
originals. Response `Content-Type` is `application/x-visoswap-pack`.

## Wire format

```
+------------------------------------+--------------+---------------------+
| uint32, big-endian, header length  | JSON header  | frame bytes, packed |
+------------------------------------+--------------+---------------------+
            4 bytes                    N bytes        concatenated
```

The header is UTF-8 JSON:

```json
{"frames":[{"key":"000051.633","offset":0,"length":35120,"status":200},
           {"key":"000051.675","offset":35120,"length":34988,"status":200},
           {"key":"000051.717","offset":0,"length":0,"status":404}]}
```

- `offset` is relative to the first byte **after** the header, never to the
  start of the response.
- `length` is 0 when `status` is not 200.
- `status` is the per-frame result: `200` present, `404` not generated yet (or
  the file vanished), `400` malformed key, `403` path escaped the project,
  `413` dropped because the pack hit the 8 MB cap. A non-200 entry is **not** an
  error for the request: during playback the next frames are usually still being
  written, and losing the whole pack would stall the video.

Header length is capped at 64 KB and the payload at 8 MB. Frames past the cap
are still listed, with `status: 413`, so the client knows they were considered.

## TypeScript

```ts
export type PackEntry = { key: string; offset: number; length: number; status: number };

export function parsePack(buf: ArrayBuffer): Map<string, Uint8Array> {
  const view = new DataView(buf);
  const headerLen = view.getUint32(0, false);          // false = big-endian
  const header = JSON.parse(
    new TextDecoder().decode(new Uint8Array(buf, 4, headerLen))
  ) as { frames: PackEntry[] };
  const body = new Uint8Array(buf, 4 + headerLen);
  const out = new Map<string, Uint8Array>();
  for (const e of header.frames) {
    if (e.status !== 200 || e.length === 0) continue;
    out.set(e.key, body.subarray(e.offset, e.offset + e.length));
  }
  return out;
}

export async function fetchPack(projectId: string, keys: string[], w = 720, q = 70) {
  const res = await fetch(`/api/projects/${projectId}/frames/pack`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ keys, w, q }),
  });
  if (!res.ok) throw new Error(`pack: ${res.status}`);
  return parsePack(await res.arrayBuffer());
}
```

`subarray` on the body is a view, not a copy, so the whole buffer stays alive as
long as any frame does — if that matters, `slice()` instead. To turn one into an
image:

```ts
const url = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
img.src = url;                              // revoke when done
```

## Swift

```swift
struct PackEntry: Decodable { let key: String; let offset: Int; let length: Int; let status: Int }
struct PackHeader: Decodable { let frames: [PackEntry] }

func parsePack(_ data: Data) throws -> [String: Data] {
    guard data.count >= 4 else { throw PackError.truncated }
    let headerLen = data.prefix(4).withUnsafeBytes { $0.load(as: UInt32.self).bigEndian }
    let start = 4 + Int(headerLen)
    guard start <= data.count else { throw PackError.truncated }
    let header = try JSONDecoder().decode(PackHeader.self, from: data[4..<start])
    let body = data.subdata(in: start..<data.count)      // rebased to 0
    var out: [String: Data] = [:]
    for e in header.frames where e.status == 200 && e.length > 0 {
        guard e.offset + e.length <= body.count else { throw PackError.truncated }
        out[e.key] = body.subdata(in: e.offset..<(e.offset + e.length))
    }
    return out
}
```

Comparing an 8 MB pack fetched over a weak link: use the original request's
`Content-Length` and stream, or just take the pack—`URLSession` buffers by
default, which is fine at these sizes.

## Compatibility

The endpoint is advertised by `GET /api/health` in `features`:

```json
{"status": "ok", "features": ["frame-variants", "frame-pack"]}
```

A client must check for `frame-pack` and fall back to
`GET /api/projects/{id}/frame/{key}?w=&q=` on older backends — an older server
returns 404 for this route, not an empty pack. Check `frame-variants` before
adding `w`/`q` to a single-frame request for the same reason.

## Why not multipart

`multipart/byteranges` and `multipart/mixed` both parse ambiguity from
boundaries appearing inside JPEG bytes; a length-prefixed header plus absolute
offsets cannot be ambiguous. A ZIP would add deflate work on a CPU already busy
encoding frames, for JPEGs that are already compressed.
