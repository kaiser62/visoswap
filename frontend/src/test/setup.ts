import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

// Auto-cleanup only registers when vitest `globals` is on; the plan keeps
// explicit imports, so we register cleanup here.
afterEach(() => {
  cleanup()
})
