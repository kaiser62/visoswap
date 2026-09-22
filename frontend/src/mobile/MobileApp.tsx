import { useState } from 'react'
import { MobileHeader } from './MobileHeader'
import { MobileStudioView } from './MobileStudioView'
import { MobileFacesView } from './MobileFacesView'
import { MobileControlsView } from './MobileControlsView'
import { MobileTakesView } from './MobileTakesView'
import { MobileQueueView } from './MobileQueueView'
import { useMedia } from '../state/MediaContext'

type MobileTab = 'studio' | 'faces' | 'controls' | 'takes' | 'queue'

interface MobileAppProps {
  onSwitchToDesktop: () => void
}

export function MobileApp({ onSwitchToDesktop }: MobileAppProps) {
  const [activeTab, setActiveTab] = useState<MobileTab>('studio')
  const { running } = useMedia()

  return (
    <div className="flex h-screen flex-col bg-bg text-text selection:bg-accent selection:text-bg">
      {/* 1. Sticky Mobile Header */}
      <MobileHeader onSwitchToDesktop={onSwitchToDesktop} />

      {/* 2. Scrollable Body Content */}
      <main className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        <div hidden={activeTab !== 'studio'}>
          <MobileStudioView onGoToFaces={() => setActiveTab('faces')} />
        </div>

        <div hidden={activeTab !== 'faces'}>
          <MobileFacesView onFaceSelected={() => setActiveTab('studio')} />
        </div>

        <div hidden={activeTab !== 'controls'}>
          <MobileControlsView />
        </div>

        <div hidden={activeTab !== 'takes'}>
          <MobileTakesView />
        </div>

        <div hidden={activeTab !== 'queue'}>
          <MobileQueueView />
        </div>
      </main>

      {/* 3. Fixed Bottom Tab Navigation (iOS TabBar Style) */}
      <nav
        className="sticky bottom-0 z-30 border-t border-line/80 bg-card/90 backdrop-blur-xl"
        style={{ paddingBottom: 'env(safe-area-inset-bottom, 0.5rem)' }}
      >
        <div className="flex h-14 items-center justify-around px-2">
          {/* Tab 1: Studio */}
          <button
            type="button"
            onClick={() => setActiveTab('studio')}
            className={`flex flex-1 flex-col items-center justify-center py-1 transition-colors ${
              activeTab === 'studio' ? 'text-accent font-semibold' : 'text-muted active:text-text'
            }`}
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={activeTab === 'studio' ? 2.2 : 1.7} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={activeTab === 'studio' ? 2.2 : 1.7} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span className="text-[10px] tracking-tight">Studio</span>
          </button>

          {/* Tab 2: Faces */}
          <button
            type="button"
            onClick={() => setActiveTab('faces')}
            className={`flex flex-1 flex-col items-center justify-center py-1 transition-colors ${
              activeTab === 'faces' ? 'text-accent font-semibold' : 'text-muted active:text-text'
            }`}
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={activeTab === 'faces' ? 2.2 : 1.7} d="M14.828 14.828a4 4 0 01-5.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span className="text-[10px] tracking-tight">Faces</span>
          </button>

          {/* Tab 3: Controls */}
          <button
            type="button"
            onClick={() => setActiveTab('controls')}
            className={`flex flex-1 flex-col items-center justify-center py-1 transition-colors ${
              activeTab === 'controls' ? 'text-accent font-semibold' : 'text-muted active:text-text'
            }`}
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={activeTab === 'controls' ? 2.2 : 1.7} d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
            </svg>
            <span className="text-[10px] tracking-tight">Controls</span>
          </button>

          {/* Tab 4: Takes */}
          <button
            type="button"
            onClick={() => setActiveTab('takes')}
            className={`flex flex-1 flex-col items-center justify-center py-1 transition-colors ${
              activeTab === 'takes' ? 'text-accent font-semibold' : 'text-muted active:text-text'
            }`}
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={activeTab === 'takes' ? 2.2 : 1.7} d="M7 4v16M17 4v16M3 8h4m10 0h4M3 12h18M3 16h4m10 0h4M4 20h16a1 1 0 001-1V5a1 1 0 00-1-1H4a1 1 0 00-1 1v14a1 1 0 001 1z" />
            </svg>
            <span className="text-[10px] tracking-tight">Takes</span>
          </button>

          {/* Tab 5: Queue / Status */}
          <button
            type="button"
            onClick={() => setActiveTab('queue')}
            className={`relative flex flex-1 flex-col items-center justify-center py-1 transition-colors ${
              activeTab === 'queue' ? 'text-accent font-semibold' : 'text-muted active:text-text'
            }`}
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={activeTab === 'queue' ? 2.2 : 1.7} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
            </svg>
            <span className="text-[10px] tracking-tight">Queue</span>
            {running && (
              <span className="absolute top-1 right-3 h-2 w-2 rounded-full bg-accent animate-ping" />
            )}
          </button>
        </div>
      </nav>
    </div>
  )
}
