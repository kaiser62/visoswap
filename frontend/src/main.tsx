import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { KeyPrompt } from './components/KeyPrompt'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
    <KeyPrompt />
  </StrictMode>,
)
