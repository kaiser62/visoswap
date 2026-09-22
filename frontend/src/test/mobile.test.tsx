import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MobileApp } from '../mobile/MobileApp';
import { SettingsProvider } from '../state/SettingsContext';
import { MediaProvider } from '../state/MediaContext';

describe('MobileApp', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ projects: [], faces: [], presets: [] }),
    }));
  });

  it('renders bottom tab navigation and switches tabs', () => {
    render(
      <SettingsProvider>
        <MediaProvider projectId={null}>
          <MobileApp onSwitchToDesktop={() => {}} />
        </MediaProvider>
      </SettingsProvider>
    );

    // Check bottom navigation tabs
    const nav = screen.getByRole('navigation');
    expect(nav).toBeDefined();

    // Studio tab active by default
    expect(screen.getByText(/^Studio$/i)).toBeDefined();
    expect(screen.getByText(/^Faces$/i)).toBeDefined();
    expect(screen.getByText(/^Controls$/i)).toBeDefined();
    expect(screen.getByText(/^Takes$/i)).toBeDefined();
    expect(screen.getByText(/^Queue$/i)).toBeDefined();

    // Switch to Faces tab
    fireEvent.click(screen.getByText(/^Faces$/i));
    expect(screen.getByText(/Face Library/i)).toBeDefined();

    // Switch to Controls tab
    fireEvent.click(screen.getByText(/^Controls$/i));
    expect(screen.getByText(/Loading schema controls/i)).toBeDefined();

    // Switch to Takes tab
    fireEvent.click(screen.getByText(/^Takes$/i));
    expect(screen.getByText(/Saved Takes/i)).toBeDefined();

    // Switch to Queue tab
    fireEvent.click(screen.getByText(/^Queue$/i));
    expect(screen.getByText(/Completed/i)).toBeDefined();
  });
});
