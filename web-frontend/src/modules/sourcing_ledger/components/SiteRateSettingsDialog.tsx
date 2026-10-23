import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import {
  getProfitActivitySettings,
  listProfitActivitySites,
  recalculateProfitActivityProducts,
  saveProfitActivitySettings,
  updateProfitActivitySite,
} from "../../profit_activity/api/profitActivityApi";

type SiteSettingField = { key: string; label: string; transform?: "percent" };

type SiteOption = {
  site_code: string;
  display_name: string;
  builtin: boolean;
  data?: Record<string, unknown>;
  [key: string]: unknown;
};

const GENERIC_FIELDS: SiteSettingField[] = [
  { key: "first_mile_rate", label: "站点头程每kg" },
  { key: "first_mile_fixed", label: "站点头程固定费" },
  { key: "domestic_fee", label: "国内操作费" },
  { key: "shipping_subsidy", label: "运费补贴" },
  { key: "end_fee", label: "尾程固定费" },
  { key: "refund_rate", label: "退款率 %", transform: "percent" },
];

// 与「利润活动」页的站点费率设置保持同一套字段：两端读写同一份 settings，
// 任一处改动都会影响另一处，并按新费率重算产品库。
const BUILTIN_FIELDS: Record<string, SiteSettingField[]> = {
  US: [
    { key: "us_first_mile_rate", label: "站点头程每kg" },
    { key: "us_first_mile_fixed", label: "站点头程固定费" },
    { key: "us_domestic_fee", label: "国内操作费" },
    { key: "us_shipping_subsidy", label: "运费补贴" },
    { key: "us_refund_rate", label: "退款率 %", transform: "percent" },
  ],
  CO: [
    { key: "co_first_mile_rate", label: "站点头程每kg" },
    { key: "co_first_mile_fixed", label: "站点头程固定费" },
    { key: "co_domestic_fee", label: "国内操作费" },
    { key: "co_shipping_subsidy", label: "运费补贴" },
    { key: "co_refund_rate", label: "退款率 %", transform: "percent" },
  ],
  EC: [
    { key: "ec_first_mile_rate", label: "站点头程每kg" },
    { key: "ec_first_mile_fixed", label: "站点头程固定费" },
    { key: "ec_domestic_fee", label: "国内操作费" },
    { key: "ec_shipping_subsidy", label: "运费补贴" },
    { key: "ec_shipping_subsidy_price_limit", label: "补贴售价上限（含）" },
    { key: "ec_refund_rate", label: "退款率 %", transform: "percent" },
  ],
  PE: [
    { key: "pe_first_mile_rate", label: "站点头程每kg" },
    { key: "pe_first_mile_fixed", label: "站点头程固定费" },
    { key: "pe_domestic_fee", label: "国内操作费" },
    { key: "pe_shipping_subsidy", label: "运费补贴" },
    { key: "pe_refund_rate", label: "退款率 %", transform: "percent" },
  ],
};

const DEFAULTS: Record<string, number> = {
  us_first_mile_rate: 72,
  us_first_mile_fixed: 5,
  us_domestic_fee: 2.5,
  us_shipping_subsidy: 21,
  us_refund_rate: 0.05,
  co_first_mile_rate: 70,
  co_first_mile_fixed: 0,
  co_domestic_fee: 2.5,
  co_shipping_subsidy: 21,
  co_refund_rate: 0.05,
  ec_first_mile_rate: 108,
  ec_first_mile_fixed: 0,
  ec_domestic_fee: 2.5,
  ec_shipping_subsidy: 15,
  ec_shipping_subsidy_price_limit: 120,
  ec_end_fee: 27,
  ec_refund_rate: 0.05,
  pe_first_mile_rate: 80,
  pe_first_mile_fixed: 0,
  pe_domestic_fee: 2.5,
  pe_shipping_subsidy: 21,
  pe_refund_rate: 0.05,
  first_mile_rate: 0,
  first_mile_fixed: 0,
  domestic_fee: 2.5,
  shipping_subsidy: 21,
  end_fee: 0,
  refund_rate: 0.05,
};

function fieldsFor(site: SiteOption | undefined): SiteSettingField[] {
  if (!site) return GENERIC_FIELDS;
  return site.builtin ? BUILTIN_FIELDS[site.site_code.toUpperCase()] ?? GENERIC_FIELDS : GENERIC_FIELDS;
}

function draftFrom(site: SiteOption | undefined, settings: Record<string, unknown>): Record<string, string> {
  // 内置站点费率存在全局 settings；自定义站点的费率字段直接挂在站点对象上。
  const source = site?.builtin ? settings : (site?.data ?? site ?? {});
  const draft: Record<string, string> = {};
  for (const field of fieldsFor(site)) {
    const raw = source[field.key] == null ? (DEFAULTS[field.key] ?? 0) : source[field.key];
    const value = Number(raw);
    const safe = Number.isFinite(value) ? value : 0;
    draft[field.key] = String(field.transform === "percent" ? safe * 100 : safe);
  }
  return draft;
}

export function SiteRateSettingsDialog({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved?: (message: string) => void;
}) {
  const [sites, setSites] = useState<SiteOption[]>([]);
  const [settings, setSettings] = useState<Record<string, unknown>>({});
  const [active, setActive] = useState("");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const currentSite = useMemo(() => sites.find((site) => site.site_code === active), [sites, active]);

  const load = useCallback(async () => {
    setBusy("load");
    setError("");
    setMessage("");
    try {
      const [settingsData, siteOptions] = await Promise.all([
        getProfitActivitySettings(),
        listProfitActivitySites() as unknown as Promise<SiteOption[]>,
      ]);
      setSettings(settingsData);
      setSites(siteOptions);
      const next = String(active || siteOptions[0]?.site_code || "US");
      setActive(next);
      setDraft(draftFrom(siteOptions.find((site) => site.site_code === next), settingsData));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
    // active 只作为首次默认值，不参与重载依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!open) return;
    void load();
  }, [open, load]);

  const selectSite = (site: SiteOption) => {
    setActive(site.site_code);
    setDraft(draftFrom(site, settings));
    setMessage("");
    setError("");
  };

  const recalculate = async () => {
    const site = currentSite?.site_code || active;
    try {
      const result = await recalculateProfitActivityProducts({ sites: [site], scope: "company" });
      return Number(result.updated || 0);
    } catch {
      const fallback = await recalculateProfitActivityProducts({ sites: [site], scope: "default" });
      return Number(fallback.updated || 0);
    }
  };

  const persist = async (values: Record<string, number>) => {
    let revision = Number(settings.revision || 0);
    const save = (extra: Record<string, unknown>) =>
      saveProfitActivitySettings({ ...extra, ...values });
    let next: Record<string, unknown>;
    try {
      next = await save({ expected_revision: revision });
    } catch (cause) {
      // 乐观锁冲突：重新取最新 revision 再存一次
      const fresh = await getProfitActivitySettings();
      revision = Number(fresh.revision || 0);
      next = await save({ expected_revision: revision });
    }
    if (!currentSite?.builtin) {
      const payload = {
        site_code: currentSite?.site_code || active,
        display_name: currentSite?.display_name || active,
        ...values,
      };
      await updateProfitActivitySite(active, payload);
    }
    setSettings(next);
    setDraft(draftFrom(currentSite, next));
    const updated = await recalculate();
    const text = `费率已保存，已按新费率重算 ${updated} 个产品库记录`;
    setMessage(text);
    onSaved?.(text);
  };

  const save = async () => {
    setBusy("save");
    setError("");
    setMessage("");
    try {
      const values: Record<string, number> = {};
      for (const field of fieldsFor(currentSite)) {
        const raw = Number(draft[field.key] || 0);
        values[field.key] = field.transform === "percent" ? raw / 100 : raw;
      }
      await persist(values);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const restoreDefault = async () => {
    setBusy("default");
    setError("");
    setMessage("");
    try {
      const values: Record<string, number> = {};
      for (const field of fieldsFor(currentSite)) values[field.key] = DEFAULTS[field.key] ?? 0;
      await persist(values);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  if (!open) return null;

  return createPortal(
    <div className="rate-dialog-mask" onClick={onClose}>
      <section className="rate-dialog" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <h2>站点费率设置</h2>
            <p>与「利润活动」共用同一份费率；保存后按当前费率重算该站点产品库与货源利润。</p>
          </div>
          <button type="button" className="ledger-close" onClick={onClose}>×</button>
        </header>

        <div className="rate-dialog-sites">
          {sites.map((site) => (
            <button
              key={site.site_code}
              type="button"
              className={`rate-dialog-site${site.site_code === active ? " is-active" : ""}`}
              onClick={() => selectSite(site)}
              disabled={Boolean(busy)}
            >
              {site.display_name || site.site_code}
            </button>
          ))}
        </div>

        <p className="ledger-hint">
          正在编辑 {currentSite?.display_name || active} 的费率；未保存过的字段按系统默认值展示。
        </p>

        <div className="rate-dialog-fields">
          {fieldsFor(currentSite).map((field) => (
            <label key={field.key}>
              <span>{field.label}</span>
              <input
                value={draft[field.key] ?? ""}
                onChange={(event) => setDraft({ ...draft, [field.key]: event.target.value })}
              />
            </label>
          ))}
        </div>

        {message && <p className="ledger-notice">{message}</p>}
        {error && <p className="ledger-error">{error}</p>}

        <footer>
          <button type="button" className="ledger-btn is-primary" onClick={() => void save()} disabled={Boolean(busy)}>
            {busy === "save" ? "保存中…" : "保存并重算"}
          </button>
          <button type="button" className="ledger-btn" onClick={() => void restoreDefault()} disabled={Boolean(busy)}>
            {busy === "default" ? "恢复中…" : "恢复默认并重算"}
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}
