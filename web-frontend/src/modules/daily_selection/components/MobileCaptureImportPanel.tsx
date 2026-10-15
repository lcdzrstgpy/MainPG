import { useCallback, useEffect, useRef, useState } from "react";

import {
  deleteMobileCapture,
  listMobileCaptures,
  markMobileCaptureImported,
  type MobileCapture,
} from "../api/mobileCaptureApi";
import { importFromLink } from "../api/dailySelectionApi";
import { toUserMessage } from "../../../transport/http/client";
import "../styles/shop-collection.css";

type MobileCaptureImportPanelProps = {
  isActive?: boolean;
  onImported?: (runId: string) => void;
  onPendingCountChange?: (count: number) => void;
};

function formatDate(value: string): string {
  if (!value) return "";
  const date = new Date(value.replace(" ", "T"));
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function captureErrorMessage(error: unknown): string {
  return toUserMessage(error instanceof Error ? error.message : String(error ?? ""));
}

/** 手机端记录的来源站点 → 采集平台标识，供后端跳过平台猜测。 */
function platformOf(capture: MobileCapture): string | undefined {
  const site = `${capture.source_site} ${capture.source_url}`.toLowerCase();
  if (site.includes("1688")) return "1688";
  if (site.includes("淘宝") || site.includes("天猫") || site.includes("taobao") || site.includes("tmall")) {
    return "taobao";
  }
  return undefined;
}

export function MobileCaptureImportPanel({
  isActive = true,
  onImported,
  onPendingCountChange,
}: MobileCaptureImportPanelProps) {
  const [captures, setCaptures] = useState<MobileCapture[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState("");
  const [importingAll, setImportingAll] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const importAllCancelled = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const page = await listMobileCaptures(200, 0, "pending");
      setCaptures(page.captures);
      onPendingCountChange?.(page.captures.length);
    } catch (requestError) {
      setError(captureErrorMessage(requestError));
    } finally {
      setLoading(false);
    }
  }, [onPendingCountChange]);

  useEffect(() => {
    if (!isActive) return;
    setLoading(true);
    void refresh();
  }, [isActive, refresh]);

  const importOne = useCallback(
    async (capture: MobileCapture): Promise<boolean> => {
      try {
        const run = await importFromLink(capture.source_url, platformOf(capture));
        await markMobileCaptureImported(capture.capture_id).catch(() => undefined);
        setCaptures((current) => {
          const next = current.filter((item) => item.capture_id !== capture.capture_id);
          onPendingCountChange?.(next.length);
          return next;
        });
        onImported?.(run.run_id);
        return true;
      } catch (requestError) {
        setError(captureErrorMessage(requestError));
        return false;
      }
    },
    [onImported, onPendingCountChange],
  );

  async function importSingle(capture: MobileCapture) {
    if (busyId || importingAll) return;
    setBusyId(capture.capture_id);
    setError("");
    setNotice("");
    const ok = await importOne(capture);
    if (ok) setNotice("已导入采集队列，可在「每日选品」中查看结果。");
    setBusyId("");
  }

  async function importAll() {
    if (importingAll || busyId || captures.length === 0) return;
    setImportingAll(true);
    importAllCancelled.current = false;
    setError("");
    setNotice("");
    let succeeded = 0;
    let failed = 0;
    for (const capture of captures) {
      if (importAllCancelled.current) break;
      const ok = await importOne(capture);
      if (ok) succeeded += 1;
      else failed += 1;
    }
    setNotice(
      importAllCancelled.current
        ? `已停止，成功导入 ${succeeded} 条。`
        : `导入完成：成功 ${succeeded} 条${failed ? `，失败 ${failed} 条` : ""}。`,
    );
    setImportingAll(false);
  }

  async function removeCapture(capture: MobileCapture) {
    if (busyId || importingAll) return;
    setBusyId(capture.capture_id);
    setError("");
    setNotice("");
    try {
      await deleteMobileCapture(capture.capture_id);
      setCaptures((current) => {
        const next = current.filter((item) => item.capture_id !== capture.capture_id);
        onPendingCountChange?.(next.length);
        return next;
      });
    } catch (requestError) {
      setError(captureErrorMessage(requestError));
    } finally {
      setBusyId("");
    }
  }

  return (
    <section className="shop-collection-panel" aria-label="手机端导入">
      <header className="shop-collection-header">
        <div className="shop-collection-title">
          <span className="iconfont icon-phone" aria-hidden="true"></span>
          <div>
            <strong>手机端采集链接</strong>
            <p>手机 App 采集的商品链接会同步到当前账号，在这里一键导入到采集队列，随后进入草稿池处理。</p>
          </div>
        </div>
        <span className="shop-collection-persistent">{loading ? "读取中…" : `${captures.length} 条待导入`}</span>
      </header>

      {(error || notice) && (
        <p className={`shop-collection-message ${error ? "is-error" : "is-success"}`} role="status">
          {error || notice}
        </p>
      )}

      <div className="shop-collection-grid">
        <div className="shop-batch-detail">
          <div className="shop-items-heading">
            <strong>待导入链接</strong>
            <span>{captures.length ? `共 ${captures.length} 条` : "暂无记录"}</span>
            <button
              type="button"
              disabled={importingAll || captures.length === 0 || Boolean(busyId)}
              onClick={() => void importAll()}
            >
              {importingAll ? "正在导入…" : "全部导入"}
            </button>
            {importingAll && (
              <button type="button" onClick={() => { importAllCancelled.current = true; }}>
                停止
              </button>
            )}
            <button type="button" disabled={importingAll || Boolean(busyId)} onClick={() => void refresh()}>
              刷新
            </button>
          </div>

          {!loading && captures.length === 0 && (
            <p className="shop-empty">
              <strong>暂无手机端采集记录</strong>
              <br />
              在手机 App「采集」页登记商品链接后，会同步到这里。
            </p>
          )}

          <div className="shop-items-list">
            {captures.map((capture) => (
              <article key={capture.capture_id}>
                <div>
                  <strong>{capture.title.trim() || capture.source_url}</strong>
                  <small>
                    {[capture.source_site || "未知来源", capture.device_name, formatDate(capture.created_at)]
                      .filter(Boolean)
                      .join(" · ")}
                  </small>
                  <small>
                    <a href={capture.source_url} target="_blank" rel="noreferrer">{capture.source_url}</a>
                  </small>
                </div>
                <div className="plugin-capture-item-links">
                  <button
                    type="button"
                    disabled={importingAll || Boolean(busyId)}
                    onClick={() => void importSingle(capture)}
                  >
                    {busyId === capture.capture_id ? "导入中…" : "导入到采集队列"}
                  </button>
                  <button
                    type="button"
                    disabled={importingAll || Boolean(busyId)}
                    onClick={() => void removeCapture(capture)}
                  >
                    删除
                  </button>
                </div>
              </article>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
