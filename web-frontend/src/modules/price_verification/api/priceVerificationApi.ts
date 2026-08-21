import { httpJson } from "../../../transport/http/client";
import type { BatchSelection, BatchSourcingState, PluginCommand, PluginSession, PrescreenSettings, QuoteBatchReviewItem, QuoteCaptureBatch, QuoteDecision, QuoteItem, QuoteRun, SkcSourceLink, SourceCandidate, SourcePreview, SourceTopProfit } from "../types";

const base = "/api/v1/price-verification";
const key = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

export const priceVerificationApi = {
  listSessions: async () => {
    const payload = await httpJson<{ sessions?: PluginSession[] }>(`${base}/plugin/sessions`);
    return Array.isArray(payload.sessions) ? payload.sessions : [];
  },
  getCommand: (id: string) => httpJson<PluginCommand>(`/plugin/commands/${encodeURIComponent(id)}`),
  getPrescreen: async () => httpJson<PrescreenSettings>(`${base}/prescreen`),
  setPrescreen: (minAdjustedPriceCny: string) => httpJson<PrescreenSettings>(`${base}/prescreen`, { method: "PUT", body: { min_adjusted_price_cny: minAdjustedPriceCny } }),
  listCaptureBatches: async () => (await httpJson<{ batches: QuoteCaptureBatch[] }>(`${base}/capture-batches`)).batches,
  createCaptureBatch: (name: string) => httpJson<QuoteCaptureBatch>(`${base}/capture-batches`, { method: "POST", body: { name, make_current: true } }),
  activateCaptureBatch: (batchId: string) => httpJson<QuoteCaptureBatch>(`${base}/capture-batches/${encodeURIComponent(batchId)}/activate`, { method: "POST" }),
  setCaptureBatchStoreName: (batchId: string, storeName: string) => httpJson<QuoteCaptureBatch>(`${base}/capture-batches/${encodeURIComponent(batchId)}/store`, { method: "PUT", body: { store_name: storeName } }),
  listCaptureBatchItems: async (batchId: string) => (await httpJson<{ batch_id: string; items: QuoteBatchReviewItem[] }>(`${base}/capture-batches/${encodeURIComponent(batchId)}/items`)).items,
  removeCaptureBatchItem: async (batchId: string, skcId: string) => httpJson<{ batch_id: string; skc_id: string; removed: number }>(`${base}/capture-batches/${encodeURIComponent(batchId)}/items?skc_id=${encodeURIComponent(skcId)}`, { method: "DELETE" }),
  stageBatchSelections: async (batchId: string, skcIds: string[], maxCandidates?: number) => (await httpJson<{ batch_id: string; selections: BatchSelection[] }>(`${base}/capture-batches/${encodeURIComponent(batchId)}/selections`, { method: "POST", body: { skc_ids: skcIds, ...(maxCandidates != null ? { max_candidates: maxCandidates } : {}) } })).selections,
  listBatchSelections: async (batchId: string) => (await httpJson<{ batch_id: string; selections: BatchSelection[] }>(`${base}/capture-batches/${encodeURIComponent(batchId)}/selections`)).selections,
  reviewBatchSelection: (batchId: string, selectionId: number, decision: BatchSelection["status"], maxCandidates: number, note?: string) => httpJson<BatchSelection>(`${base}/capture-batches/${encodeURIComponent(batchId)}/selections/${selectionId}/review`, { method: "POST", body: { decision, max_candidates: maxCandidates, note: note ?? "" } }),
  prepareBatchSourcing: (batchId: string, skcIds: string[]) => httpJson<BatchSourcingState>(`${base}/capture-batches/${encodeURIComponent(batchId)}/sourcing/prepare`, { method: "POST", body: { skc_ids: skcIds } }),
  getBatchSourcingState: (batchId: string) => httpJson<BatchSourcingState>(`${base}/capture-batches/${encodeURIComponent(batchId)}/sourcing`),
  sourceBatchSelections: (batchId: string, skcIds?: string[]) => httpJson<SourcePreview>(`${base}/capture-batches/${encodeURIComponent(batchId)}/sourcing`, { method: "POST", body: { idempotency_key: key(), skc_ids: skcIds ?? [] } }),
  addManualSourceCandidate: (batchId: string, skcId: string, sourceUrl: string) => httpJson<BatchSourcingState>(`${base}/capture-batches/${encodeURIComponent(batchId)}/sourcing/manual-candidate`, { method: "POST", body: { skc_id: skcId, source_url: sourceUrl } }),
  selectBatchSourceCandidate: (batchId: string, skcId: string, candidate: SourceCandidate, priceCny?: string, weightKg?: string) => httpJson<BatchSourcingState>(`${base}/capture-batches/${encodeURIComponent(batchId)}/sourcing/candidates`, { method: "POST", body: { skc_id: skcId, candidate, ...(priceCny !== undefined ? { price_cny: priceCny } : {}), ...(weightKg !== undefined ? { weight_kg: weightKg } : {}) } }),
  unselectBatchSourceCandidate: (batchId: string, skcId: string, offerId: string) => httpJson<BatchSourcingState>(`${base}/capture-batches/${encodeURIComponent(batchId)}/sourcing/candidates?skc_id=${encodeURIComponent(skcId)}&offer_id=${encodeURIComponent(offerId)}`, { method: "DELETE" }),
  completeBatchSourcing: (batchId: string) => httpJson<BatchSourcingState>(`${base}/capture-batches/${encodeURIComponent(batchId)}/sourcing/complete`, { method: "POST" }),
  previewSourceProfit: (batchId: string, payload: { site: string; selling_price: string | number; price: string | number; moq?: string | number | null; domestic_freight?: string | number | null; weight_kg?: string | number }) => httpJson<SourceTopProfit>(`${base}/capture-batches/${encodeURIComponent(batchId)}/source-profit-preview`, { method: "POST", body: payload }),
  listSkcSourceLinks: async (batchId: string, skcId?: string) => (await httpJson<{ links: SkcSourceLink[] }>(`${base}/capture-batches/${encodeURIComponent(batchId)}/skc-source-links${skcId ? `?skc_id=${encodeURIComponent(skcId)}` : ""}`)).links,
  removeSkcSourceLink: (batchId: string, linkId: number) => httpJson<SkcSourceLink>(`${base}/capture-batches/${encodeURIComponent(batchId)}/skc-source-links/${linkId}`, { method: "DELETE" }),
  queueQuote: (sessionId: string) => httpJson<PluginCommand>(`${base}/quote-runs`, { method: "POST", body: { session_id: sessionId, payload: {}, idempotency_key: key() } }),
  materializeQuote: (commandId: string) => httpJson<QuoteRun>(`${base}/quote-runs`, { method: "POST", body: { command_id: commandId } }),
  getQuoteItems: (runId: string) => httpJson<{ run_id: string; quotes: QuoteItem[] }>(`${base}/quote-runs/${encodeURIComponent(runId)}/items`),
  listDecisions: async (runId: string) => (await httpJson<{ decisions: QuoteDecision[] }>(`${base}/quote-runs/${encodeURIComponent(runId)}/decisions`)).decisions,
  recordDecision: (runId: string, quoteKey: string, decision: QuoteDecision["decision"], note: string) => httpJson<QuoteDecision>(`${base}/quote-runs/${encodeURIComponent(runId)}/decisions`, { method: "POST", body: { quote_key: quoteKey, decision, note } }),
  exportQuote: (runId: string) => httpJson<{ workbook_path: string }>(`${base}/quote-runs/${encodeURIComponent(runId)}/exports`, { method: "POST" }),
  queueSourcing: (sessionId: string, quoteRunId: string) => httpJson<PluginCommand>(`${base}/sourcing-runs`, { method: "POST", body: { session_id: sessionId, quote_run_id: quoteRunId, max_quotes: 50, idempotency_key: key() } }),
  materializeSourcing: (commandId: string, quoteRunId?: string) => httpJson<SourcePreview>("/local/source-discovery/browser-search/preview", { method: "POST", body: quoteRunId ? { source_command_id: commandId, quote_run_id: quoteRunId } : { source_command_id: commandId } }),
};
