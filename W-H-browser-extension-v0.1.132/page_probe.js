(function installWorkbenchNetworkProbe() {
  const PROBE_VERSION = "0.1.117";
  if (window.__temuWorkbenchNetworkProbe?.installed && window.__temuWorkbenchNetworkProbe.version === PROBE_VERSION) return;

  const MAX_CAPTURED = 500;
  const utils = window.WorkbenchNetworkProbeUtils;
  const captures = [];
  const originalFetch = window.fetch;
  const OriginalXHR = window.XMLHttpRequest;

  function record(input) {
    try {
      captures.push(input);
      if (captures.length > MAX_CAPTURED) {
        captures.splice(0, captures.length - MAX_CAPTURED);
      }
    } catch (_error) {
      // Page probe must never break the merchant page.
    }
  }

  async function serializeRequestBody(body) {
    try {
      if (body == null) return "";
      if (typeof body === "string") return body;
      if (body instanceof URLSearchParams) return body.toString();
      if (body instanceof Blob) return await body.text();
      if (body instanceof ArrayBuffer) return new TextDecoder().decode(body);
      if (ArrayBuffer.isView(body)) return new TextDecoder().decode(body);
      if (body instanceof FormData) {
        const entries = {};
        for (const [key, value] of body.entries()) {
          entries[key] = typeof File !== "undefined" && value instanceof File ? `[File:${value.name}]` : String(value);
        }
        return JSON.stringify(entries);
      }
      return String(body || "");
    } catch (_error) {
      return "";
    }
  }

  async function serializeFetchRequest(resource, init) {
    try {
      if (init && Object.prototype.hasOwnProperty.call(init, "body")) {
        return await serializeRequestBody(init.body);
      }
      if (typeof Request !== "undefined" && resource instanceof Request) {
        const clone = resource.clone();
        return await clone.text();
      }
    } catch (_error) {
      return "";
    }
    return "";
  }

  window.fetch = async function workbenchFetchProbe(resource, init) {
    const requestUrl = typeof resource === "string" ? resource : resource?.url;
    const method = init?.method || resource?.method || "GET";
    const requestText = await serializeFetchRequest(resource, init);
    let response;
    try {
      response = await originalFetch.apply(this, arguments);
    } catch (_error) {
      // 请求被拦截（广告拦截类插件 cancel）或网络失败时记录失败原因，
      // 再原样抛出，保持页面行为不变。
      record({
        url: String(requestUrl || ""),
        method,
        status: 0,
        contentType: "",
        requestText,
        error: "blocked",
        responseText: "",
        capturedAt: new Date().toISOString()
      });
      throw _error;
    }
    try {
      const clone = response.clone();
      const contentType = clone.headers.get("content-type") || "";
      if (/json|text/i.test(contentType || "")) {
        clone.text().then((text) => {
          record({
            url: String(requestUrl || ""),
            method,
            status: response.status,
            contentType,
            requestText,
            responseText: text,
            capturedAt: new Date().toISOString()
          });
        }).catch(() => {});
      }
    } catch (_error) {
      // Keep the original fetch response untouched.
    }
    return response;
  };

  function serializeSyncRequestBody(body) {
    try {
      if (body == null) return "";
      if (typeof body === "string") return body;
      if (body instanceof URLSearchParams) return body.toString();
      if (body instanceof ArrayBuffer) return new TextDecoder().decode(body);
      if (ArrayBuffer.isView(body)) return new TextDecoder().decode(body);
      if (body instanceof FormData) {
        const entries = {};
        for (const [key, value] of body.entries()) {
          entries[key] = typeof File !== "undefined" && value instanceof File ? `[File:${value.name}]` : String(value);
        }
        return JSON.stringify(entries);
      }
      return String(body || "");
    } catch (_error) {
      return "";
    }
  }

  const originalXHROpen = OriginalXHR.prototype.open;
  const originalXHRSend = OriginalXHR.prototype.send;
  OriginalXHR.prototype.open = function workbenchPatchedOpen(method, url) {
    this.__workbenchProbeRequest = {
      method: method || "GET",
      url: String(url || ""),
      requestText: ""
    };
    return originalXHROpen.apply(this, arguments);
  };
  OriginalXHR.prototype.send = function workbenchPatchedSend(body) {
    const request = this.__workbenchProbeRequest || { method: "GET", url: "", requestText: "" };
    request.requestText = serializeSyncRequestBody(body);
    this.__workbenchProbeRequest = request;
    this.addEventListener("load", function workbenchXhrLoadProbe() {
      try {
        const contentType = this.getResponseHeader("content-type") || "";
        // responseType='json'（或 blob/arraybuffer 但 content-type 为 json）时
        // responseText 为空串，数据在 this.response 里；取不到时跳过不误录空串。
        let responseText = this.responseType === "json" && typeof this.response !== "undefined" && this.response != null
          ? (typeof this.response === "string" ? this.response : JSON.stringify(this.response))
          : this.responseText;
        if ((/json|text/i.test(contentType || "") || (responseText && /^[\s\r\n]*[\[{]/.test(String(responseText)))) && typeof responseText === "string" && responseText !== "") {
          const capturedRequest = this.__workbenchProbeRequest || request;
          record({
            url: capturedRequest.url || "",
            method: capturedRequest.method || "GET",
            status: this.status,
            contentType,
            requestText: capturedRequest.requestText || "",
            responseText,
            capturedAt: new Date().toISOString()
          });
        }
      } catch (_error) {
        // Some responses do not expose responseText; ignore them.
      }
    }, { once: true });
    this.addEventListener("error", function workbenchXhrErrorProbe() {
      // 请求被拦截（广告拦截类插件 cancel）或网络失败时记录失败原因。
      try {
        const capturedRequest = this.__workbenchProbeRequest || request;
        record({
          url: capturedRequest.url || "",
          method: capturedRequest.method || "GET",
          status: 0,
          contentType: "",
          requestText: capturedRequest.requestText || "",
          error: "blocked",
          responseText: "",
          capturedAt: new Date().toISOString()
        });
      } catch (_error) {
        // Ignore probe errors.
      }
    }, { once: true });
    return originalXHRSend.apply(this, arguments);
  };

  // 1688（mtop/h5api/laputa 等）的 SKU/详情数据通过 <script> 标签 JSONP 加载，
  // fetch/XHR 补丁捕获不到。这里用 MutationObserver 监听新增脚本，包装其
  // callback 全局函数，把 JSONP 返回的数据对象写入 captures 供提取方使用。
  function installJsonpProbe() {
    try {
      if (typeof MutationObserver === "undefined") return;
      const seenCallbacks = new Set();
      const wrapCallback = (script) => {
        try {
          if (!script || script.__workbenchJsonpWatched) return;
          script.__workbenchJsonpWatched = true;
          let callbackName = "";
          try {
            callbackName = new URL(script.src || "", location.href).searchParams.get("callback")
              || new URL(script.src || "", location.href).searchParams.get("jsonp")
              || new URL(script.src || "", location.href).searchParams.get("cb")
              || "";
          } catch (_error) {}
          if (!callbackName || !/^[A-Za-z_$][\w$]*$/.test(callbackName)) return;
          if (seenCallbacks.has(callbackName)) return;
          const original = window[callbackName];
          if (typeof original !== "function") return;
          seenCallbacks.add(callbackName);
          window[callbackName] = function workbenchJsonpProbe(...args) {
            try {
              const payload = args && args.length ? args[0] : undefined;
              if (payload && typeof payload === "object" && !(payload instanceof ArrayBuffer)) {
                let responseText = "";
                try { responseText = JSON.stringify(payload); } catch (_error) { responseText = ""; }
                if (responseText) {
                  record({
                    url: script.src || "",
                    method: "JSONP",
                    status: 200,
                    contentType: "application/json",
                    requestText: "",
                    responseText,
                    capturedAt: new Date().toISOString()
                  });
                }
              }
            } catch (_error) {}
            try {
              return original.apply(this, args);
            } catch (_error) {
              return undefined;
            }
          };
        } catch (_error) {}
      };
      const scanNode = (node) => {
        if (!node || node.nodeType !== 1) return;
        if (String(node.tagName || "").toUpperCase() === "SCRIPT" && node.src) {
          wrapCallback(node);
          return;
        }
        if (node.querySelectorAll) {
          Array.from(node.querySelectorAll("script[src]")).forEach(wrapCallback);
        }
      };
      // 已有脚本兜底（页面动态追加的脚本可能已存在）。
      Array.from(document.querySelectorAll("script[src]")).forEach(wrapCallback);
      const observer = new MutationObserver((mutations) => {
        try {
          for (const mutation of mutations) {
            for (const node of mutation.addedNodes || []) scanNode(node);
          }
        } catch (_error) {}
      });
      observer.observe(document.documentElement || document, { childList: true, subtree: true });
    } catch (_error) {
      // JSONP 探针失败不阻断页面与采集主流程。
    }
  }

  installJsonpProbe();

  window.__temuWorkbenchNetworkProbe = {
    installed: true,
    version: PROBE_VERSION,
    captures,
    startedAt: new Date().toISOString(),
    getBlockedCaptures(sinceMs) {
      // 返回最近 sinceMs 毫秒内被拦截/失败的请求记录（用于诊断兼容性问题）。
      const cutoff = Date.now() - (Number(sinceMs) || 60000);
      return captures
        .filter((item) => item?.error === "blocked")
        .filter((item) => {
          try {
            return new Date(item.capturedAt).getTime() >= cutoff;
          } catch (_error) {
            return true;
          }
        })
        .slice(-40);
    },
    getCaptures(captureType, since, limit) {
      const filtered = captures.filter((item) => {
        if (since && item.capturedAt < since) return false;
        return !utils || utils.shouldCaptureUrl(item.url, captureType);
      });
      const normalized = filtered.map((item) => utils ? utils.normalizeRecord(item, captureType) : item);
      return utils ? utils.trimRecords(normalized, limit) : normalized.slice(-50);
    },
    // 1688 新版详情页（od-* SPA）的 SKU/价格接口可能不在 shouldCaptureUrl 的
    // URL 规则内，直接按"是 JSON 响应"读取最近记录，交由提取方自行识别规格结构。
    getAllJsonCaptures(limit) {
      const normalized = captures
        .filter((item) => item && !item.error && item.responseText)
        .map((item) => utils ? utils.normalizeRecord(item, "product_capture_to_workbench") : item)
        .filter((item) => item && item.responseJson);
      return utils ? utils.trimRecords(normalized, limit) : normalized.slice(-50);
    }
  };
})();

