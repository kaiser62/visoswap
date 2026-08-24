import { expect, test, type Page } from '@playwright/test'
import { existsSync } from 'node:fs'
import { basename, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

/** The whole Studio workspace against the real backend (plan 05.1-08 Task 3).
 *
 * Plans 04–07 built the shell, ingest, the overlay, jobs, results and the
 * gallery, and each was proven under jsdom with a faked backend. This spec is
 * the other half: one browser, one real FastAPI process, one real engine, from
 * an empty project to an exported take.
 *
 * **Media is not committed.** A run needs a clip with a detectable face in it
 * and a face image the recognition model can embed; `testsrc` has no face and a
 * run over it would prove nothing. So the spec uses the same local, gitignored
 * fixtures the sealed engine runner uses, overridable by the same environment
 * variables, and fails with the path it wanted rather than skipping.
 */

// The project is ESM, so `__dirname` does not exist here.
const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const VIDEO_PATH = resolve(
  process.env.VISOSWAP_TEST_VIDEO ||
    resolve(REPO_ROOT, 'tests/media/17f0d620_rosh_generate_135bda4c9686.mp4'),
)
const FACE_PATH = resolve(
  process.env.VISOSWAP_TEST_SOURCE ||
    resolve(REPO_ROOT, 'tests/media/598004cb_tonima.JPG'),
)

/** The take name the recorder derives: `project_YYYYMMDD-HHMMSS_face.mp4`.
 *  Matched as a shape, never as a literal — the timestamp is the wall clock,
 *  and that it separates one run from the next is the D-12 property under
 *  test. */
const TAKE_NAME = /^e2e-studio.*_\d{8}-\d{6}_.+\.mp4(\.partial)?$/

test.beforeAll(() => {
  for (const [label, path, envVar] of [
    ['target video', VIDEO_PATH, 'VISOSWAP_TEST_VIDEO'],
    ['source face', FACE_PATH, 'VISOSWAP_TEST_SOURCE'],
  ] as const) {
    if (!existsSync(path)) {
      throw new Error(
        `${label} not found at ${path} — point ${envVar} at one, or see ` +
          'docs/engine-test-assets.md. A run without a detectable face proves nothing.',
      )
    }
  }
})

async function openFreshProject(
  page: Page,
  request: import('@playwright/test').APIRequestContext,
) {
  const resp = await request.post('/api/projects', { data: { name: 'e2e-studio' } })
  const project = await resp.json()
  await page.goto(`/?project=${project.id}`)
  await page.waitForSelector('[data-key]')
  return project.id as string
}

/** Upload the clip and wait for the player to actually hold it. */
async function loadVideo(page: Page) {
  await page.getByTestId('media-file-input').setInputFiles(VIDEO_PATH)
  await expect(page.getByTestId('media-video-filename')).toHaveText(
    basename(VIDEO_PATH),
    { timeout: 30_000 },
  )
  await expect(page.getByTestId('player-video')).toBeVisible()
}

/** Upload the face, then activate it. Activation is what binds it to the
 *  project — an uploaded face sitting in the global library changes nothing. */
async function activateFace(page: Page) {
  await page.getByTestId('face-upload-input').setInputFiles(FACE_PATH)
  const tile = page.getByRole('button', { name: /^Activate face / }).first()
  await expect(tile).toBeVisible({ timeout: 30_000 })
  await tile.click()
  await expect(tile).toHaveAttribute('aria-pressed', 'true', { timeout: 15_000 })
}

test.describe('the Studio workspace end to end', () => {
  test('a fresh project shows both empty states', async ({ page, request }) => {
    await openFreshProject(page, request)
    await expect(page.getByText('Load a target video to begin.')).toBeVisible()
    await expect(
      page.getByText('Add a video by choosing a local file or pointing at a URL.'),
    ).toBeVisible()
    // The face library is machine-global (D-03), so "empty" for a fresh
    // project means no face is *bound* to it — not that the library has none.
    // Asserting an empty strip would only hold until some other run uploaded a
    // face, which is the library working as designed.
    await expect(page.locator('button[data-testid^="face-"][aria-pressed="true"]')).toHaveCount(0)
  })

  test('video and face ingest fill the player and the face strip', async ({
    page,
    request,
  }) => {
    await openFreshProject(page, request)
    await loadVideo(page)
    await activateFace(page)
    await expect(page.getByTestId('media-error')).toBeHidden()
    await expect(page.getByTestId('face-upload-error')).toBeHidden()
  })

  test('preview renders one frame over the player without starting a run', async ({
    page,
    request,
  }) => {
    await openFreshProject(page, request)
    await loadVideo(page)
    await activateFace(page)

    await page.getByTestId('preview-button').click()
    // A real swap on a real card: generous, because this is the engine, not a mock.
    await expect(page.getByTestId('overlay-image')).toBeVisible({ timeout: 120_000 })
    await expect(page.getByTestId('preview-error')).toBeHidden()
    // Preview never touches the scheduler (D-09): the run button is still armed.
    await expect(page.getByTestId('btn-start-run')).toBeEnabled()
  })

  test('a bounded run reports jobs, leaves a recording and a take', async ({
    page,
    request,
  }) => {
    test.setTimeout(300_000)
    await openFreshProject(page, request)
    await loadVideo(page)
    await activateFace(page)

    // Bound the work: interval mode at one frame a second, over a range of a
    // couple of seconds, so the spec finishes on the engine's real cadence
    // instead of generating the whole clip.
    await page.getByTestId('mode-interval').click()
    await page.getByTestId('interval-input').fill('1')
    await page.getByTestId('interval-input').blur()
    await page.getByTestId('seek-seconds').fill('0')
    await page.getByTestId('seek-seconds').press('Enter')
    await page.getByTestId('btn-set-start').click()
    await page.getByTestId('seek-seconds').fill('2')
    await page.getByTestId('seek-seconds').press('Enter')
    await page.getByTestId('btn-set-end').click()

    await page.getByTestId('btn-start-run').click()
    await expect(page.getByTestId('btn-stop-run')).toBeEnabled({ timeout: 30_000 })
    // The jobs card is fed by the socket, so counts moving is also proof the
    // websocket connected.
    await expect(page.getByTestId('jobs-card')).toBeVisible()
    await expect(page.getByTestId('jobs-idle')).toBeHidden({ timeout: 120_000 })

    await page.getByTestId('btn-stop-run').click()
    await expect(page.getByTestId('btn-start-run')).toBeEnabled({ timeout: 60_000 })

    // The results card offers the recording, partial or finished — both are
    // downloadable, and neither is ever mislabelled.
    await expect(page.getByTestId('results-download')).toBeVisible({ timeout: 60_000 })
    await expect(page.getByTestId('results-label')).toHaveText(
      /Recording in progress|Finished recording/,
    )

    // The export lands in the gallery under the derived name (D-12).
    await expect
      .poll(
        async () => {
          const takes = await (await request.get('/api/takes')).json()
          return takes.map((t: { name: string }) => t.name)
        },
        { timeout: 60_000, message: 'the run left no take in the output folder' },
      )
      .toEqual(expect.arrayContaining([expect.stringMatching(TAKE_NAME)]))

    await page.getByTestId('view-gallery').click()
    await expect(page.getByTestId('take-item').first()).toBeVisible({ timeout: 30_000 })
  })

  test('the gallery round trip unmounts no schema control', async ({ page, request }) => {
    await openFreshProject(page, request)
    const before = await page.locator('[data-key]').count()
    expect(before).toBe(201)

    await page.getByTestId('view-gallery').click()
    await expect(page.getByTestId('gallery-view')).toBeVisible()
    // Hidden, not unmounted: the studio's controls are still in the document
    // while the user is looking at takes.
    expect(await page.locator('[data-key]').count()).toBe(before)

    await page.getByTestId('view-studio').click()
    await expect(page.getByTestId('studio-view')).toBeVisible()
    expect(await page.locator('[data-key]').count()).toBe(before)
  })
})
