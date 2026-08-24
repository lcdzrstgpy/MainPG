(function attachOneboundPageCapture(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.OneboundPageCapture = api;
})(typeof self !== "undefined" ? self : globalThis, function createOneboundPageCapture() {
  const DEFAULT_MAX_SCROLL_PASSES = 12;
  const DEFAULT_SCROLL_WAIT_MS = 650;
  const DEFAULT_MAX_ITEMS = 80;
  const DEFAULT_CONCURRENCY = 3;

  function createSingleflight() {
    const inFlight = new Map();
    return Object.freeze({
      run(key, operation) {
        if (inFlight.has(key)) return inFlight.get(key);
        let task;
        try {
          task = Promise.resolve(operation());
        } catch (error) {
          task = Promise.reject(error);
        }
        const tracked = task.finally(() => {
          if (inFlight.get(key) === tracked) inFlight.delete(key);
        });
        inFlight.set(key, tracked);
        return tracked;
      }
    });
  }

  function canonicalizeOfferUrl(value) {
    let parsed;
    try {
      parsed = new URL(String(value || "").trim(), "https://detail.1688.com/");
    } catch (_error) {
      return "";
    }
    if (parsed.hostname.toLowerCase() !== "detail.1688.com") return "";
    const match = parsed.pathname.match(/^\/offer\/(\d+)(?:\.html?)?\/?$/i);
    return match ? `https://detail.1688.com/offer/${match[1]}.html` : "";
  }

  function canonicalizeOfferUrls(values, limit = DEFAULT_MAX_ITEMS) {
    const output = [];
    const seen = new Set();
    for (const value of Array.isArray(values) ? values : []) {
      const sourceUrl = canonicalizeOfferUrl(value);
      if (!sourceUrl || seen.has(sourceUrl)) continue;
      seen.add(sourceUrl);
      output.push(sourceUrl);
      if (output.length >= limit) break;
    }
    return output;
  }

  function canonicalizeFailedUrls(values) {
    const rawUrls = (Array.isArray(values) ? values : [])
      .map((item) => (item && typeof item === "object" ? (item.source_url || item.url || item.link || "") : item));
    return canonicalizeOfferUrls(rawUrls, Number.MAX_SAFE_INTEGER);
  }

  function createSessionJobStore(storageArea, storageKey = "onebound_page_capture_prepared_jobs_v1") {
    if (!storageArea || typeof storageArea.get !== "function" || typeof storageArea.set !== "function" || typeof storageArea.remove !== "function") {
      throw new Error("onebound_page_capture_session_storage_required");
    }
    let mutationQueue = Promise.resolve();
    const readAll = async () => {
      const saved = await storageArea.get(storageKey);
      const jobs = saved?.[storageKey];
      return jobs && typeof jobs === "object" ? jobs : {};
    };
    const mutate = (operation) => {
      const next = mutationQueue.then(operation, operation);
      mutationQueue = next.catch(() => {});
      return next;
    };
    const safeJob = (job) => ({
      batch_token: String(job?.batch_token || ""),
      source_tab_id: Number(job?.source_tab_id || 0) || null,
      page_url: String(job?.page_url || ""),
      source_urls: canonicalizeOfferUrls(job?.source_urls, DEFAULT_MAX_ITEMS),
      plan: {
        batch_token: String(job?.plan?.batch_token || job?.batch_token || ""),
        total_count: Number(job?.plan?.total_count || 0) || 0,
        existing_count: Number(job?.plan?.existing_count || 0) || 0,
        pending_count: Number(job?.plan?.pending_count || 0) || 0,
        pending_urls: canonicalizeOfferUrls(job?.plan?.pending_urls, DEFAULT_MAX_ITEMS),
        existing_offer_ids: Array.isArray(job?.plan?.existing_offer_ids) ? job.plan.existing_offer_ids.map(String) : [],
        expires_at: job?.plan?.expires_at || null,
        statusText: String(job?.plan?.statusText || "")
      },
      connection_identity: {
        http_base: String(job?.connection_identity?.http_base || ""),
        session_token: String(job?.connection_identity?.session_token || "")
      },
      state: "prepared"
    });
    return {
      async save(job) {
        const safe = safeJob(job);
        if (!safe.batch_token || !safe.connection_identity.http_base || !safe.connection_identity.session_token) {
          throw new Error("invalid_onebound_page_capture_prepared_job");
        }
        return mutate(async () => {
          const jobs = await readAll();
          jobs[safe.batch_token] = safe;
          await storageArea.set({ [storageKey]: jobs });
          return safe;
        });
      },
      async load(batchToken) {
        await mutationQueue;
        const jobs = await readAll();
        const saved = jobs[String(batchToken || "")];
        return saved ? safeJob(saved) : null;
      },
      async list() {
        await mutationQueue;
        const jobs = await readAll();
        return Object.values(jobs).map(safeJob);
      },
      async remove(batchToken) {
        return mutate(async () => {
          const jobs = await readAll();
          delete jobs[String(batchToken || "")];
          if (Object.keys(jobs).length) await storageArea.set({ [storageKey]: jobs });
          else await storageArea.remove(storageKey);
        });
      }
    };
  }

  function createStartGate() {
    const activeTokens = new Set();
    return {
      begin(batchToken) {
        const token = String(batchToken || "").trim();
        if (!token || activeTokens.has(token)) return false;
        activeTokens.add(token);
        return true;
      },
      release(batchToken) {
        activeTokens.delete(String(batchToken || "").trim());
      }
    };
  }

  function jobsForCancellation(jobs, sourceTabId) {
    const activeJobs = Array.isArray(jobs) ? jobs.filter((job) => !job?.finished && !job?.cancelled) : [];
    return sourceTabId == null ? activeJobs : activeJobs.filter((job) => job?.source_tab_id === sourceTabId);
  }

  function preparedJobsForReplacement(jobs, sourceTabId) {
    return (Array.isArray(jobs) ? jobs : [])
      .filter((job) => !job?.finished && !job?.cancelled)
      .filter((job) => job?.state === "prepared" && job?.source_tab_id === sourceTabId);
  }

  function connectionIdentityMatches(expected, actual) {
    return Boolean(expected && actual
      && String(expected.http_base || "") === String(actual.http_base || "")
      && String(expected.session_token || "") === String(actual.session_token || ""));
  }

  async function restoreCancellableJob({ store, batchToken, sourceTabId, connection } = {}) {
    if (!store || typeof store.load !== "function" || sourceTabId == null) {
      return { job: null, error: "onebound_page_capture_not_prepared" };
    }
    const job = await store.load(batchToken);
    if (!job || job.source_tab_id !== sourceTabId) {
      return { job: null, error: "onebound_page_capture_not_prepared" };
    }
    if (!connectionIdentityMatches(job.connection_identity, connection)) {
      return { job: null, error: "onebound_page_capture_connection_changed" };
    }
    return { job, error: "" };
  }

  // This function is intentionally self-contained: Chrome serializes it into the
  // target page through chrome.scripting.executeScript without its module closure.
  async function scanPage(options = {}) {
    const page = options.page || window;
    const maxScrollPasses = Math.max(1, Number(options.maxScrollPasses) || DEFAULT_MAX_SCROLL_PASSES);
    const scrollWaitMs = Math.max(0, Number(options.scrollWaitMs) || DEFAULT_SCROLL_WAIT_MS);
    const maxItems = Math.max(1, Number(options.maxItems) || DEFAULT_MAX_ITEMS);
    const sleep = typeof options.sleep === "function"
      ? options.sleep
      : (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const originalScrollY = Number(page.scrollY || page.pageYOffset || 0);
    const sourceUrls = [];
    const seen = new Set();
    let scrollScanPasses = 0;
    let unchangedPasses = 0;
    let stopReason = "max_scroll_passes";

    const scanLinks = () => {
      let added = 0;
      const anchors = page.document?.querySelectorAll?.("a[href]") || [];
      for (const anchor of anchors) {
        let parsed;
        try {
          parsed = new URL(anchor.href || anchor.getAttribute?.("href") || "", page.location?.href || "https://detail.1688.com/");
        } catch (_error) {
          continue;
        }
        const hostname = parsed.hostname.toLowerCase();
        if (hostname !== "1688.com" && !hostname.endsWith(".1688.com")) continue;

        const offerIds = [];
        const pathMatch = hostname === "detail.1688.com"
          ? parsed.pathname.match(/^\/offer\/(\d+)(?:\.html?)?\/?$/i)
          : null;
        if (pathMatch) offerIds.push(pathMatch[1]);
        const normalizedPath = parsed.pathname.toLowerCase();
        const isMobileOfferLink = hostname === "detail.m.1688.com"
          && normalizedPath === "/page/index.html";
        const isSimilarOfferLink = hostname === "s.1688.com"
          && /^\/selloffer\/similar_search\.html?$/.test(normalizedPath);
        if (isMobileOfferLink || isSimilarOfferLink) {
          for (const [name, value] of parsed.searchParams) {
            const normalizedName = String(name || "").toLowerCase();
            if (isMobileOfferLink ? normalizedName !== "offerid" : normalizedName !== "offerids") continue;
            for (const offerId of String(value || "").split(",")) {
              if (/^\d{6,20}$/.test(offerId)) offerIds.push(offerId);
            }
          }
        }

        for (const offerId of offerIds) {
          const sourceUrl = `https://detail.1688.com/offer/${offerId}.html`;
          if (seen.has(sourceUrl)) continue;
          seen.add(sourceUrl);
          sourceUrls.push(sourceUrl);
          added += 1;
          if (sourceUrls.length >= maxItems) break;
        }
        if (sourceUrls.length >= maxItems) break;
      }
      return added;
    };

    try {
      if (scanLinks() >= 0 && sourceUrls.length >= maxItems) {
        stopReason = "max_items";
      } else {
        for (let pass = 0; pass < maxScrollPasses; pass += 1) {
          const documentHeight = Math.max(
            Number(page.document?.documentElement?.scrollHeight || 0),
            Number(page.document?.body?.scrollHeight || 0)
          );
          page.scrollTo?.(0, documentHeight);
          await sleep(scrollWaitMs);
          scrollScanPasses += 1;
          const added = scanLinks();
          if (sourceUrls.length >= maxItems) {
            stopReason = "max_items";
            break;
          }
          unchangedPasses = added > 0 ? 0 : unchangedPasses + 1;
          if (unchangedPasses >= 3) {
            stopReason = "no_new_urls";
            break;
          }
        }
      }
    } finally {
      page.scrollTo?.(0, originalScrollY);
    }

    return {
      page_url: String(page.location?.href || ""),
      source_urls: sourceUrls,
      total_candidates: sourceUrls.length,
      scroll_scan_passes: scrollScanPasses,
      stop_reason: stopReason
    };
  }

  function itemCounts(response) {
    const payload = response && typeof response === "object" ? response : {};
    const outcome = String(payload.outcome || "").toLowerCase();
    return {
      created: Number(payload.created_count ?? payload.created ?? (outcome === "created" ? 1 : 0)) || 0,
      refreshed: Number(payload.refreshed_count ?? payload.refreshed ?? (outcome === "refreshed" ? 1 : 0)) || 0,
      skipped: Number(payload.skipped_count ?? payload.skipped ?? (outcome === "skipped" ? 1 : 0)) || 0
    };
  }

  async function runCapture(options = {}) {
    const request = options.request;
    if (typeof request !== "function") throw new Error("onebound_page_capture_request_required");
    const sessionToken = String(options.sessionToken || "").trim();
    if (!sessionToken) throw new Error("missing_plugin_session");
    const pageUrl = String(options.pageUrl || "").trim();
    const control = options.control || { cancelled: false };
    const notify = typeof options.onProgress === "function" ? options.onProgress : async () => {};
    const requestedUrls = canonicalizeOfferUrls(options.sourceUrls, options.maxItems || DEFAULT_MAX_ITEMS);
    const prepared = options.prepared || await request("prepare", {
      session_token: sessionToken,
      page_url: pageUrl,
      source_urls: requestedUrls
    });
    if (!prepared?.ok && !prepared?.batch_token) {
      return { ok: false, ...(prepared || {}), failed_urls: [] };
    }
    const batchToken = String(prepared.batch_token || "").trim();
    if (!batchToken) throw new Error("missing_batch_token");
    const pendingUrls = canonicalizeOfferUrls(
      Array.isArray(prepared.pending_urls) ? prepared.pending_urls : requestedUrls,
      options.maxItems || DEFAULT_MAX_ITEMS
    );
    const total = Number(prepared.pending_count ?? pendingUrls.length) || pendingUrls.length;
    const existingCount = Number(prepared.existing_count || 0) || 0;
    let completed = 0;
    let createdCount = 0;
    let refreshedCount = 0;
    let skippedCount = existingCount;
    const failed = [];
    const emit = async (phase, statusText) => notify({
      type: "ONEBOUND_PAGE_CAPTURE_PROGRESS",
      batch_token: batchToken,
      phase,
      completed,
      total,
      created_count: createdCount,
      refreshed_count: refreshedCount,
      skipped_count: skippedCount,
      failed_count: failed.length,
      unprocessed_count: Math.max(0, pendingUrls.length - completed),
      statusText
    });

    await emit("starting", prepared.statusText || "正在启动 OneBound 页面采集");
    const started = await request("start", { session_token: sessionToken, batch_token: batchToken });
    if (started?.ok === false) {
      let finishError = "";
      try {
        const finish = await request("finish", { session_token: sessionToken, batch_token: batchToken, cancelled: true });
        if (finish?.ok === false) {
          finishError = String(finish.error || finish.statusText || "onebound_page_capture_finish_failed");
        }
      } catch (error) {
        finishError = String(error?.message || error || "onebound_page_capture_finish_failed");
      }
      return {
        ok: false,
        ...started,
        batch_token: batchToken,
        failed_urls: canonicalizeFailedUrls(started.failed_urls),
        ...(finishError ? { finish_error: finishError, help: [started.help, started.statusText, finishError].filter(Boolean).join("; ") } : {})
      };
    }
    const concurrency = Math.max(1, Math.min(DEFAULT_CONCURRENCY, Number(options.concurrency) || DEFAULT_CONCURRENCY));
    let nextIndex = 0;
    const worker = async () => {
      while (!control.cancelled && !control.stop_dispatch && nextIndex < pendingUrls.length) {
        const sourceUrl = pendingUrls[nextIndex];
        nextIndex += 1;
        try {
          const item = await request("item", {
            session_token: sessionToken,
            batch_token: batchToken,
            source_url: sourceUrl
          });
          if (item?.ok === false) {
            failed.push({ source_url: sourceUrl, error: String(item.error || item.statusText || "item_capture_failed") });
            if ([401, 403, 429, 500, 502, 503, 504].includes(Number(item.status || item.http_status || 0))) {
              control.stop_dispatch = true;
              control.fatal_error = String(item.error || item.statusText || `item_http_${item.status}`);
            }
          } else {
            const counts = itemCounts(item);
            createdCount += counts.created;
            refreshedCount += counts.refreshed;
            skippedCount += counts.skipped;
          }
        } catch (error) {
          failed.push({ source_url: sourceUrl, error: String(error?.message || error || "item_capture_failed") });
        } finally {
          completed += 1;
          await emit("capturing", `OneBound 页面采集进度 ${completed}/${total}`);
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(concurrency, pendingUrls.length) }, worker));
    const cancelled = Boolean(control.cancelled || control.stop_dispatch);
    let finishError = null;
    let finish = null;
    let finishNotFound = false;
    try {
      finish = await request("finish", {
        session_token: sessionToken,
        batch_token: batchToken,
        cancelled
      });
      if (finish?.ok === false) {
        if (Number(finish.status || finish.http_status || 0) === 404) {
          finishNotFound = true;
        } else {
          finishError = String(finish.error || finish.statusText || "onebound_page_capture_finish_failed");
        }
      }
    } catch (error) {
      finishError = String(error?.message || error || "onebound_page_capture_finish_failed");
    }
    const response = finish && typeof finish === "object" ? finish : {};
    const finalCreated = Number(response.created_count ?? createdCount) || 0;
    const finalRefreshed = Number(response.refreshed_count ?? refreshedCount) || 0;
    const finalSkipped = Number(response.skipped_count ?? skippedCount) || 0;
    const finalFailedUrls = canonicalizeOfferUrls([
      ...canonicalizeFailedUrls(response.failed_urls),
      ...failed.map((item) => item.source_url)
    ], Number.MAX_SAFE_INTEGER);
    const result = {
      ...response,
      ok: response.ok !== false && !finishError,
      batch_token: batchToken,
      cancelled: Boolean(response.cancelled ?? cancelled),
      captured_count: finalCreated + finalRefreshed,
      created_count: finalCreated,
      refreshed_count: finalRefreshed,
      skipped_count: finalSkipped,
      failed_count: Math.max(Number(response.failed_count || 0) || 0, finalFailedUrls.length),
      unprocessed_count: Number(response.unprocessed_count ?? Math.max(0, pendingUrls.length - completed)) || 0,
      failed_urls: finalFailedUrls,
      failed,
      completed,
      total,
      ...(finishNotFound ? { finish_not_found: true } : {})
    };
    if (finishError) {
      result.error = "onebound_page_capture_finish_failed";
      result.help = finishError;
      result.finish_error = finishError;
      result.statusText = "OneBound 页面采集完成，但批次收口失败";
    } else if (finishNotFound) {
      result.statusText = "OneBound 页面采集完成，批次已由后端清理";
    } else if (control.fatal_error) {
      result.error = "onebound_page_capture_fatal_item_failed";
      result.help = control.fatal_error;
      result.statusText = response.statusText || "OneBound 页面采集因后端限制提前停止";
    }
    await emit("finished", result.statusText || response.statusText || (result.cancelled ? "OneBound 页面采集已中断" : "OneBound 页面采集完成"));
    return result;
  }

  return Object.freeze({
    canonicalizeOfferUrl,
    canonicalizeOfferUrls,
    createSingleflight,
    createSessionJobStore,
    createStartGate,
    jobsForCancellation,
    preparedJobsForReplacement,
    connectionIdentityMatches,
    restoreCancellableJob,
    scanPage,
    runCapture
  });
});
