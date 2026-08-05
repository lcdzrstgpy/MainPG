(function installWorkbenchNetworkProbe() {
  const PROBE_VERSION = "0.1.109";
  if (window.__temuWorkbenchNetworkProbe?.installed && window.__temuWorkbenchNetworkProbe.version === PROBE_VERSION) return;

  const MAX_CAPTURED = 200;
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
    const response = await originalFetch.apply(this, arguments);
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
        if (/json|text/i.test(contentType || "") && typeof this.responseText === "string") {
          const capturedRequest = this.__workbenchProbeRequest || request;
          record({
            url: capturedRequest.url || "",
            method: capturedRequest.method || "GET",
            status: this.status,
            contentType,
            requestText: capturedRequest.requestText || "",
            responseText: this.responseText,
            capturedAt: new Date().toISOString()
          });
        }
      } catch (_error) {
        // Some responses do not expose responseText; ignore them.
      }
    }, { once: true });
    return originalXHRSend.apply(this, arguments);
  };

  window.__temuWorkbenchNetworkProbe = {
    installed: true,
    version: PROBE_VERSION,
    captures,
    startedAt: new Date().toISOString(),
    getCaptures(captureType, since, limit) {
      const filtered = captures.filter((item) => {
        if (since && item.capturedAt < since) return false;
        return !utils || utils.shouldCaptureUrl(item.url, captureType);
      });
      const normalized = filtered.map((item) => utils ? utils.normalizeRecord(item, captureType) : item);
      return utils ? utils.trimRecords(normalized, limit) : normalized.slice(-50);
    }
  };
})();
