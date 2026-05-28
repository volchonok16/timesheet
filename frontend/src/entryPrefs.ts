const PREFS_KEY = 'tfs-timesheet:prefs'

export type EntryPrefs = {
  role: string
  activity: string
  costProject?: string
}

export function loadEntryPrefs(): EntryPrefs | null {
  try {
    const raw = localStorage.getItem(PREFS_KEY)
    if (!raw) return null
    return JSON.parse(raw) as EntryPrefs
  } catch {
    return null
  }
}

export function saveEntryPrefs(prefs: EntryPrefs) {
  localStorage.setItem(PREFS_KEY, JSON.stringify(prefs))
}

export const TIME_PRESETS = [
  { label: '8ч', hours: 8, minutes: 0 },
  { label: '4ч', hours: 4, minutes: 0 },
  { label: '1ч', hours: 1, minutes: 0 },
  { label: '30м', hours: 0, minutes: 30 },
] as const
