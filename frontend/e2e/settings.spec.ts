import { expect, test, type Page } from '@playwright/test'

/** Browser E2E for FRONTEND-01 criteria 1–3 against the real backend.
 *
 * The server under test serves the built frontend at / (single-container mode);
 * playwright.config.ts reuses an already-running instance or starts one.
 */

async function openFreshProject(page: Page, request: import('@playwright/test').APIRequestContext) {
  const resp = await request.post('/api/projects', { data: { name: 'e2e-playwright' } })
  const project = await resp.json()
  await page.goto(`/?project=${project.id}`)
  // All controls render once schema + settings resolve.
  await page.waitForSelector('[data-key]')
  return project.id as string
}

test.describe('FRONTEND-01 in a real browser', () => {
  test('criterion 1: exactly 201 controls render, grouped with a sidebar', async ({
    page,
    request,
  }) => {
    await openFreshProject(page, request)
    const count = await page.locator('[data-key]').count()
    expect(count).toBe(201)

    const sidebar = page.locator('nav[aria-label="Settings sections"]')
    await expect(sidebar).toBeVisible()
    // Tier sections are present (Global + Project group headings).
    await expect(sidebar.getByText('Global', { exact: true })).toBeVisible()
    await expect(sidebar.getByText('Project', { exact: true })).toBeVisible()
  })

  test('gates: Brightness is lock-disabled until Color Enable flips', async ({
    page,
    request,
  }) => {
    await openFreshProject(page, request)
    const brightness = page.locator('input[type="number"][aria-label="Brightness"]')
    await expect(brightness).toBeDisabled()

    await page.locator('[data-key="ColorEnableToggle"]').click()
    await expect(brightness).toBeEnabled()

    // Typing an out-of-bounds number clamps to the schema max (the save-fix).
    await brightness.fill('999')
    await brightness.blur()
    await expect(brightness).toHaveValue('2')

    // Flip the parent back off -> the child locks again.
    await page.locator('[data-key="ColorEnableToggle"]').click()
    await expect(brightness).toBeDisabled()
  })

  test('criterion 2: presets A and with AUD open a diff modal and apply', async ({
    page,
    request,
  }) => {
    await openFreshProject(page, request)
    const presetsResp = await request.get('/api/presets')
    const { presets } = await presetsResp.json()
    const names = presets.map((p: { name: string }) => p.name)
    expect(names).toEqual(expect.arrayContaining(['A', 'with AUD']))

    const selector = page.getByLabel('Preset')

    // Preset A: diff modal opens; Cancel closes without applying errors.
    await selector.selectOption({ label: 'A' })
    const dialogA = page.getByRole('dialog', { name: 'Apply preset A' })
    await expect(dialogA).toBeVisible()
    await expect(dialogA.getByText(/Changes \d+ setting\(s\)/)).toBeVisible()
    await dialogA.getByRole('button', { name: 'Cancel' }).click()
    await expect(dialogA).toBeHidden()

    // Preset with AUD: Apply commits; modal closes; no failure banner.
    await selector.selectOption({ label: 'with AUD' })
    const dialogB = page.getByRole('dialog', { name: 'Apply preset with AUD' })
    await expect(dialogB).toBeVisible()
    await dialogB.getByRole('button', { name: 'Apply' }).click()
    await expect(dialogB).toBeHidden()
    await expect(page.getByText("Couldn't apply the preset")).toBeHidden()
  })

  test('criterion 3: change -> Save Changes -> reload restores from the project tier', async ({
    page,
    request,
  }) => {
    const projectId = await openFreshProject(page, request)
    const toggle = page.locator('[data-key="AutoColorEnableToggle"]')
    await expect(toggle).not.toBeChecked()

    await toggle.click()
    const saveBtn = page.getByRole('button', { name: /Save Changes \(1\)/ })
    await saveBtn.click()
    // Dirty cleared on success: the button reads (0) and disables.
    await expect(page.getByRole('button', { name: /Save Changes \(0\)/ })).toBeDisabled({
      timeout: 15_000,
    })

    // Server-side proof before touching the browser again.
    const settings = await (await request.get(`/api/projects/${projectId}/settings`)).json()
    expect(settings.values.AutoColorEnableToggle).toBe(true)

    await page.reload()
    await page.waitForSelector('[data-key]')
    await expect(page.locator('[data-key="AutoColorEnableToggle"]')).toBeChecked()
    await expect(page.getByRole('button', { name: /Save Changes \(0\)/ })).toBeDisabled()
  })

  test('root URL without ?project: edits still persist (project auto-selected)', async ({
    page,
  }) => {
    // The reported bug: opening / with no ?project left saves as silent no-ops.
    await page.goto('/')
    await page.waitForSelector('[data-key]')

    // A project was auto-selected and synced into the URL for reloads.
    const url = new URL(page.url())
    expect(url.searchParams.get('project')).toMatch(/^[0-9a-f]{32}$/)

    const toggle = page.locator('[data-key="AutoColorEnableToggle"]')
    const initialChecked = await toggle.isChecked()
    await toggle.click()
    await page
      .getByRole('button', { name: /Save Changes \(1\)/ })
      .click()
    await expect(
      page.getByRole('button', { name: /Save Changes \(0\)/ }),
    ).toBeDisabled({ timeout: 15_000 })

    await page.reload()
    await page.waitForSelector('[data-key]')
    const afterReload = await page
      .locator('[data-key="AutoColorEnableToggle"]')
      .isChecked()
    expect(afterReload).toBe(!initialChecked)
    // No failure banner appeared at any point.
    await expect(page.getByText("Couldn't save your changes")).toBeHidden()
  })
})
