// ============================================================================
// The Settings panel's tabs and its General tab's sections, as data: the panel
// (ConnectionsWindow), every place that opens it, and the tests read this one
// source. The top bar's gear opens GENERAL; a deep link names the tab it lands
// on (the model picker's "Add a connection" lands on AI PROVIDERS).
// A new General section is one more entry in GENERAL_SECTIONS plus the setting
// behind each row on the server (boltjar/settings.py). Pure, so it is testable
// without React.
// ============================================================================

export type SettingsTab = "general" | "providers" | "secrets";

export interface SettingsTabDef {
  id: SettingsTab;
  label: string;
  icon: string;
}

/** The tabs, in the order the panel shows them. */
export const SETTINGS_TABS: readonly SettingsTabDef[] = [
  { id: "general", label: "General", icon: "options-outline" },
  { id: "providers", label: "AI Providers", icon: "server-outline" },
  { id: "secrets", label: "Secrets", icon: "lock-closed-outline" },
];

/** The tab the top bar's gear opens. */
export const GEAR_TAB: SettingsTab = "general";

/** Where each deep link into the panel lands. */
export const SETTINGS_LINKS = {
  /** the model picker's "Add a connection" row */
  addConnection: "providers",
  /** the command palette's "Open AI Providers" */
  openConnections: "providers",
  /** the command palette's "Open Settings" */
  openSettings: "general",
} as const satisfies Record<string, SettingsTab>;

/** The tab the panel opens on: the one a link asks for, else the gear's. */
export function openingTab(asked?: SettingsTab | null): SettingsTab {
  return asked && SETTINGS_TABS.some((t) => t.id === asked) ? asked : GEAR_TAB;
}

/** A setting the server keeps (GET/PATCH /api/settings). */
export type SettingName = "launch_with_system" | "start_ollama" | "resume_workflows";

/** One toggle row: the setting, its label, and one plain line saying what it does. */
export interface SettingRow {
  name: SettingName;
  label: string;
  hint: string;
}

export interface SettingsSection {
  id: string;
  title: string;
  icon: string;
  /** a few words under the title */
  summary: string;
  rows: readonly SettingRow[];
}

export const GENERAL_SECTIONS: readonly SettingsSection[] = [
  {
    id: "startup",
    title: "Startup",
    icon: "power-outline",
    summary: "what happens when Boltjar starts",
    rows: [
      {
        name: "launch_with_system",
        label: "Launch with system",
        hint: "Start Boltjar when you log in to this computer, without opening the browser.",
      },
      {
        name: "start_ollama",
        label: "Start Ollama with Boltjar",
        hint: "Start Ollama when it is installed and not running; Boltjar stops it again when it exits.",
      },
      {
        name: "resume_workflows",
        label: "Resume workflows after launch",
        hint: "Turn back On the workflows that were On when Boltjar last stopped.",
      },
    ],
  },
];

/** What the server answers for GET and PATCH /api/settings. */
export interface SettingsInfo {
  settings: Record<SettingName, boolean>;
  /** this browser runs on the computer Boltjar runs on */
  here: boolean;
  /** the settings only such a browser may change: they start a program there */
  local_only: SettingName[];
  autostart: {
    enabled: boolean;
    /** where the login entry lives (~ for the home folder) */
    where: string;
    /** another Boltjar install that entry starts, else null */
    other: string | null;
    system: "windows" | "macos" | "xdg";
  };
}

/** Whether this browser may change `name`: every setting but the ones that
 *  start a program on Boltjar's computer, which only a browser there may. */
export function mayChange(info: SettingsInfo, name: SettingName): boolean {
  return info.here || !info.local_only.includes(name);
}

/** The line under a setting this browser may not change. */
export const ELSEWHERE_NOTE = "change this on the computer Boltjar runs on";

/** The settings a fresh install has: every one off. */
export const SETTINGS_DEFAULTS: Record<SettingName, boolean> = {
  launch_with_system: false,
  start_ollama: false,
  resume_workflows: false,
};
