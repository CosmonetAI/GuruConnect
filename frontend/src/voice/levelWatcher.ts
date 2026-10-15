export interface LevelSettings { thresholdPercent: number; seconds: number }
export const defaultLevelSettings: LevelSettings = { thresholdPercent: 5, seconds: 5 }

// Matches the recorder's meter scale: RMS level * 6, clamped to 0-1.
export const levelPercent = (rms: number) => Math.min(100, Math.max(0, rms * 6 * 100))

export interface LevelWatcher { push(rms: number, nowMs: number): boolean; reset(): void }

// Reports low input only after the level stays under the threshold for the configured duration.
export function createLevelWatcher(settings: () => LevelSettings): LevelWatcher {
  let quietSince: number | null = null
  return {
    push(rms, nowMs) {
      const { thresholdPercent, seconds } = settings()
      if (levelPercent(rms) >= thresholdPercent) { quietSince = null; return false }
      quietSince ??= nowMs
      return nowMs - quietSince >= seconds * 1000
    },
    reset() { quietSince = null },
  }
}