// ============================================================================
// GeneralSettings: the Settings panel's General tab. One card per section of
// GENERAL_SECTIONS (lib/settingsTabs), built from the provider card shell, and
// one shared Knob toggle per setting with a plain line under it saying what it
// does. Every setting lives on the server (GET/PATCH /api/settings); a change
// is sent at once and the toggle shows what the server answered.
//
// "Launch with system" is read from disk by the server (the login entry exists
// and starts this install), so its row also says where that entry lives. Only a
// browser on the same computer may change it; elsewhere the toggle is shown but
// does not flip, and the row says why.
// ============================================================================
import { useEffect, useState } from "react";
import { Icon } from "../lib/icons";
import { Knob } from "./canvas/Knob";
import { serverError } from "../lib/serverGraph";
import {
  GENERAL_SECTIONS,
  SETTINGS_DEFAULTS,
  type SettingName,
  type SettingsInfo,
} from "../lib/settingsTabs";

export function GeneralSettings() {
  const [info, setInfo] = useState<SettingsInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState<SettingName | null>(null);
  const [rowError, setRowError] = useState<{ name: SettingName; message: string } | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/api/settings");
        if (!res.ok) throw new Error(await serverError(res));
        const json = (await res.json()) as SettingsInfo;
        if (alive) { setInfo(json); setLoadError(null); }
      } catch (err) {
        if (alive) setLoadError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => { alive = false; };
  }, []);

  async function change(name: SettingName, value: boolean) {
    if (saving) return;
    setSaving(name);
    setRowError(null);
    try {
      const res = await fetch("/api/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [name]: value }),
      });
      if (!res.ok) throw new Error(await serverError(res));
      setInfo((await res.json()) as SettingsInfo);
    } catch (err) {
      setRowError({ name, message: err instanceof Error ? err.message : String(err) });
    } finally {
      setSaving(null);
    }
  }

  if (loadError) return <div className="insp-problem">{loadError}</div>;

  const values = info?.settings ?? SETTINGS_DEFAULTS;
  const auto = info?.autostart;

  return (
    <>
      {GENERAL_SECTIONS.map((section) => (
        <div key={section.id} className="prov-card">
          <div className="prov-card-head">
            <div
              className="prov-brand-tile"
              style={{ background: "color-mix(in srgb, var(--accent) 14%, transparent)", border: "1px solid color-mix(in srgb, var(--accent) 36%, transparent)" }}
            >
              <Icon name={section.icon} style={{ color: "var(--accent)" }} />
            </div>
            <div className="prov-name-block">
              <span className="prov-name">{section.title}</span>
              <span className="prov-modality">{section.summary}</span>
            </div>
          </div>
          <div className="prov-card-body set-rows">
            {section.rows.map((row) => {
              const isLaunch = row.name === "launch_with_system";
              const locked = !info || (isLaunch && auto !== undefined && !auto.editable);
              return (
                <div key={row.name} className={`set-row ${saving === row.name ? "saving" : ""}`}>
                  <Knob
                    label={row.label}
                    kind="bool"
                    value={values[row.name]}
                    default={false}
                    disabled={locked}
                    onChange={(v) => void change(row.name, v === true)}
                  />
                  <div className="set-hint">{row.hint}</div>
                  {isLaunch && auto && auto.enabled && (
                    <div className="set-where">
                      <Icon name="document-outline" />
                      <span>entry at {auto.where}</span>
                    </div>
                  )}
                  {isLaunch && auto && !auto.enabled && auto.other && (
                    <div className="set-where warn">
                      <Icon name="warning-outline" />
                      <span>
                        another Boltjar install starts at login ({auto.other}); turning this on
                        starts this one instead
                      </span>
                    </div>
                  )}
                  {isLaunch && auto && !auto.editable && (
                    <div className="set-where">
                      <Icon name="information-circle-outline" />
                      <span>change this on the computer Boltjar runs on</span>
                    </div>
                  )}
                  {rowError?.name === row.name && <div className="insp-problem">{rowError.message}</div>}
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </>
  );
}
