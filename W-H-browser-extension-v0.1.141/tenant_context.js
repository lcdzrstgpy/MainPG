(function installWorkbenchTenantContext(globalScope) {
  "use strict";

  const COMPANY_CODE_PATTERN = /^[0-9]{3}$/;
  const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  const CONTROL_CHARACTER_PATTERN = /[\u0000-\u001f\u007f]/;
  const ENCODED_SEPARATOR_PATTERN = /%(?:2f|5c)/i;

  class TenantContextError extends Error {
    constructor(code, message) {
      super(`${code}: ${message}`);
      this.name = "TenantContextError";
      this.code = code;
    }
  }

  function fail(code, message) {
    throw new TenantContextError(code, message);
  }

  function isPlainObject(value) {
    return typeof value === "object" && value !== null && !Array.isArray(value);
  }

  function decodeRepeatedly(value) {
    let decoded = value;
    try {
      for (let pass = 0; pass < 4; pass += 1) {
        const next = decodeURIComponent(decoded);
        if (next === decoded) return next;
        decoded = next;
      }
      return decoded;
    } catch (_error) {
      return null;
    }
  }

  function safePath(pathname) {
    if (
      typeof pathname !== "string"
      || !pathname.startsWith("/")
      || pathname.includes("?")
      || pathname.includes("#")
      || pathname.includes("\\")
      || pathname.includes("//")
      || CONTROL_CHARACTER_PATTERN.test(pathname)
      || ENCODED_SEPARATOR_PATTERN.test(pathname)
    ) {
      return false;
    }
    for (const segment of pathname.split("/")) {
      const decoded = decodeRepeatedly(segment);
      if (
        decoded === null
        || decoded === "."
        || decoded === ".."
        || decoded.includes("/")
        || decoded.includes("\\")
        || CONTROL_CHARACTER_PATTERN.test(decoded)
      ) {
        return false;
      }
    }
    return true;
  }

  function canonicalEntryBaseUrl(value) {
    const raw = String(value || "").trim().replace(/\/$/, "");
    let parsed;
    try {
      parsed = new URL(raw);
    } catch (_error) {
      return fail("workbench_entry_invalid", "Workbench entry must be an absolute URL.");
    }
    if (
      (parsed.protocol !== "https:" && parsed.protocol !== "http:")
      || parsed.username
      || parsed.password
      || parsed.search
      || parsed.hash
      || !safePath(parsed.pathname)
    ) {
      return fail("workbench_entry_invalid", "Workbench entry is not canonical.");
    }
    const pathname = parsed.pathname === "/" ? "" : parsed.pathname;
    let companyCode = "001";
    let mode = "legacy_001";
    if (pathname) {
      const match = /^\/t\/([0-9]{3})$/.exec(pathname);
      if (!match || match[1] === "000" || match[1] === "001") {
        return fail("workbench_entry_invalid", "Tenant entry must be /t/{three ASCII digits} for company 002+.");
      }
      companyCode = match[1];
      mode = "tenant_capsule";
    }
    return Object.freeze({
      mode,
      companyCode,
      basePath: pathname,
      origin: parsed.origin,
      httpBase: `${parsed.origin}${pathname}`
    });
  }

  function normalizeEntryBaseUrl(value) {
    return canonicalEntryBaseUrl(value).httpBase;
  }

  function isTenantEntryUrl(value) {
    try {
      return canonicalEntryBaseUrl(value).mode === "tenant_capsule";
    } catch (_error) {
      return false;
    }
  }

  function isLoopbackHttpEntryUrl(value) {
    try {
      const entry = canonicalEntryBaseUrl(value);
      const parsed = new URL(entry.httpBase);
      return parsed.protocol === "http:"
        && (parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost");
    } catch (_error) {
      return false;
    }
  }

  function deriveTenantEntryBaseUrl(pageUrl, trustedRootUrl) {
    let page;
    let trustedRoot;
    try {
      page = new URL(String(pageUrl || ""));
      trustedRoot = canonicalEntryBaseUrl(trustedRootUrl);
    } catch (_error) {
      return null;
    }
    if (
      trustedRoot.mode !== "legacy_001"
      || page.origin !== trustedRoot.origin
      || !safePath(page.pathname)
    ) {
      return null;
    }
    const match = /^\/t\/([0-9]{3})(?:\/|$)/.exec(page.pathname);
    if (!match || match[1] === "000" || match[1] === "001") return null;
    return `${page.origin}/t/${match[1]}`;
  }

  function canonicalUuid(value) {
    if (typeof value !== "string" || value !== value.toLowerCase() || !UUID_PATTERN.test(value)) {
      return fail("tenant_context_invalid", "tenant_id must be a canonical lowercase UUID.");
    }
    return value;
  }

  function canonicalCompanyCode(value) {
    if (typeof value !== "string" || !COMPANY_CODE_PATTERN.test(value) || value === "000") {
      return fail("tenant_context_invalid", "company_code must be three ASCII digits and cannot be 000.");
    }
    return value;
  }

  function canonicalHttpBase(value, entry) {
    if (typeof value !== "string" || value.trim() !== value || value === "" || value.includes("\\")) {
      return fail("tenant_context_invalid", "http_base is invalid.");
    }
    let parsed;
    try {
      parsed = value.startsWith("/") ? new URL(value, entry.origin) : new URL(value);
    } catch (_error) {
      return fail("tenant_context_invalid", "http_base is invalid.");
    }
    const expectedPath = entry.basePath || "/";
    if (
      (parsed.protocol !== "https:" && parsed.protocol !== "http:")
      || parsed.origin !== entry.origin
      || parsed.username
      || parsed.password
      || parsed.search
      || parsed.hash
      || parsed.pathname !== expectedPath
      || !safePath(parsed.pathname)
    ) {
      return fail("tenant_context_invalid", "http_base must be the canonical same-origin tenant base.");
    }
    return entry.httpBase;
  }

  function canonicalWebSocketBase(value, entry) {
    if (typeof value !== "string" || value.trim() !== value || value === "" || value.includes("\\")) {
      return fail("tenant_context_invalid", "ws_base is invalid.");
    }
    const httpOrigin = new URL(entry.origin);
    const expectedProtocol = httpOrigin.protocol === "https:" ? "wss:" : "ws:";
    const wsOrigin = `${expectedProtocol}//${httpOrigin.host}`;
    let parsed;
    try {
      parsed = value.startsWith("/") ? new URL(value, wsOrigin) : new URL(value);
    } catch (_error) {
      return fail("tenant_context_invalid", "ws_base is invalid.");
    }
    const expectedPath = entry.basePath || "/";
    if (
      parsed.protocol !== expectedProtocol
      || parsed.host !== httpOrigin.host
      || parsed.username
      || parsed.password
      || parsed.search
      || parsed.hash
      || parsed.pathname !== expectedPath
      || !safePath(parsed.pathname)
    ) {
      return fail("tenant_context_invalid", "ws_base must be the canonical same-origin tenant WebSocket base.");
    }
    return `${wsOrigin}${entry.basePath}`;
  }

  function legacyRootContext(entry) {
    const origin = new URL(entry.origin);
    const wsProtocol = origin.protocol === "https:" ? "wss:" : "ws:";
    return Object.freeze({
      schema_version: 1,
      mode: "legacy_001",
      tenant_id: null,
      company_code: "001",
      base_path: "",
      http_base: entry.origin,
      ws_base: `${wsProtocol}//${origin.host}`
    });
  }

  function resolveServerTenantContext(rawContext, requestedEntryUrl) {
    const entry = canonicalEntryBaseUrl(requestedEntryUrl);
    if (rawContext === undefined || rawContext === null) {
      if (entry.mode === "legacy_001") return legacyRootContext(entry);
      return fail("tenant_context_required", "Tenant entries require a server tenant_context.");
    }
    if (!isPlainObject(rawContext) || rawContext.schema_version !== 1) {
      return fail("tenant_context_invalid", "Unsupported tenant context schema.");
    }

    const companyCode = canonicalCompanyCode(rawContext.company_code);
    const expectedBasePath = entry.basePath;
    if (
      rawContext.mode !== entry.mode
      || companyCode !== entry.companyCode
      || rawContext.base_path !== expectedBasePath
    ) {
      return fail("tenant_context_mismatch", "Server tenant context does not match the requested entry.");
    }
    const tenantId = canonicalUuid(rawContext.tenant_id);
    const httpBase = canonicalHttpBase(rawContext.http_base, entry);
    const wsBase = canonicalWebSocketBase(rawContext.ws_base, entry);
    return Object.freeze({
      schema_version: 1,
      mode: entry.mode,
      tenant_id: tenantId,
      company_code: companyCode,
      base_path: expectedBasePath,
      http_base: httpBase,
      ws_base: wsBase
    });
  }

  function validateResolvedTenantContext(context) {
    if (!isPlainObject(context) || context.schema_version !== 1) {
      return fail("tenant_context_invalid", "Stored tenant context is invalid.");
    }
    const entry = canonicalEntryBaseUrl(context.http_base);
    if (context.mode === "legacy_001" && context.tenant_id === null) {
      if (entry.mode !== "legacy_001" || context.company_code !== "001" || context.base_path !== "") {
        return fail("tenant_context_invalid", "Stored legacy tenant context is invalid.");
      }
      const legacy = legacyRootContext(entry);
      if (context.ws_base !== legacy.ws_base) {
        return fail("tenant_context_invalid", "Stored legacy WebSocket base is invalid.");
      }
      return legacy;
    }
    return resolveServerTenantContext(context, context.http_base);
  }

  function connectionCandidateBaseUrls(preferred, fallbackBaseUrls, options = {}) {
    let preferredEntry;
    try {
      preferredEntry = canonicalEntryBaseUrl(preferred);
    } catch (_error) {
      return [];
    }
    if (preferredEntry.mode === "tenant_capsule") return [preferredEntry.httpBase];
    if (!isLoopbackHttpEntryUrl(preferredEntry.httpBase) || options.allowLoopbackFallback !== true) {
      return [preferredEntry.httpBase];
    }

    const output = [];
    const seen = new Set();
    for (const candidate of [preferredEntry.httpBase, ...(Array.isArray(fallbackBaseUrls) ? fallbackBaseUrls : [])]) {
      try {
        const entry = canonicalEntryBaseUrl(candidate);
        if (
          entry.mode !== "legacy_001"
          || !isLoopbackHttpEntryUrl(entry.httpBase)
          || seen.has(entry.httpBase)
        ) continue;
        seen.add(entry.httpBase);
        output.push(entry.httpBase);
      } catch (_error) {
        // Invalid fallback candidates are ignored, never repaired into another origin.
      }
    }
    return output;
  }

  function validSessionId(value) {
    if (Number.isInteger(value) && value > 0) return true;
    return typeof value === "string" && /^[A-Za-z0-9._:-]{1,128}$/.test(value);
  }

  function validSessionToken(value) {
    return typeof value === "string" && value.length > 0 && value.length <= 2048 && !/\s/.test(value);
  }

  function createConnectionContext(tenantContext, sessionId, sessionToken) {
    const trusted = validateResolvedTenantContext(tenantContext);
    if (!validSessionId(sessionId) || !validSessionToken(sessionToken)) {
      return fail("connection_context_invalid", "Plugin session identity is invalid.");
    }
    return Object.freeze({
      ...trusted,
      session_id: sessionId,
      session_token: sessionToken
    });
  }

  function validateConnectionContext(connectionContext) {
    if (!isPlainObject(connectionContext)) {
      return fail("connection_context_invalid", "Connection context is missing.");
    }
    return createConnectionContext(
      connectionContext,
      connectionContext.session_id,
      connectionContext.session_token
    );
  }

  function migrateLegacyConnectionSettings(settings) {
    if (!isPlainObject(settings) || !settings.sessionId || !settings.sessionToken) return null;
    let entry;
    try {
      entry = canonicalEntryBaseUrl(settings.baseUrl);
    } catch (_error) {
      return null;
    }
    if (entry.mode !== "legacy_001") return null;
    try {
      return createConnectionContext(legacyRootContext(entry), settings.sessionId, settings.sessionToken);
    } catch (_error) {
      return null;
    }
  }

  function assertServerTenantContext(connectionContext, rawServerContext) {
    const connection = validateConnectionContext(connectionContext);
    let serverContext;
    try {
      serverContext = resolveServerTenantContext(rawServerContext, connection.http_base);
    } catch (error) {
      if (error instanceof TenantContextError && error.code === "tenant_context_required") throw error;
      return fail("tenant_context_mismatch", "Server tenant context is invalid or does not match the connection.");
    }
    for (const field of ["mode", "tenant_id", "company_code", "base_path", "http_base", "ws_base"]) {
      if (serverContext[field] !== connection[field]) {
        return fail("tenant_context_mismatch", "Server tenant context changed during the plugin session.");
      }
    }
    return serverContext;
  }

  function validatedEndpoint(endpoint) {
    if (
      typeof endpoint !== "string"
      || !endpoint.startsWith("/")
      || endpoint.startsWith("//")
      || endpoint.includes("#")
      || endpoint.includes("\\")
      || CONTROL_CHARACTER_PATTERN.test(endpoint)
    ) {
      return fail("tenant_endpoint_invalid", "Endpoint must be a safe root-relative path.");
    }
    const queryIndex = endpoint.indexOf("?");
    const pathname = queryIndex === -1 ? endpoint : endpoint.slice(0, queryIndex);
    if (!safePath(pathname)) {
      return fail("tenant_endpoint_invalid", "Endpoint contains an unsafe path.");
    }
    return endpoint;
  }

  function buildHttpUrl(context, endpoint) {
    return `${validateResolvedTenantContext(context).http_base}${validatedEndpoint(endpoint)}`;
  }

  function buildEntryHttpUrl(entryUrl, endpoint) {
    return `${canonicalEntryBaseUrl(entryUrl).httpBase}${validatedEndpoint(endpoint)}`;
  }

  function buildWebSocketUrl(context, endpoint) {
    return `${validateResolvedTenantContext(context).ws_base}${validatedEndpoint(endpoint)}`;
  }

  const api = Object.freeze({
    TenantContextError,
    assertServerTenantContext,
    buildEntryHttpUrl,
    buildHttpUrl,
    buildWebSocketUrl,
    canonicalEntryBaseUrl,
    connectionCandidateBaseUrls,
    createConnectionContext,
    deriveTenantEntryBaseUrl,
    isTenantEntryUrl,
    isLoopbackHttpEntryUrl,
    migrateLegacyConnectionSettings,
    normalizeEntryBaseUrl,
    resolveServerTenantContext,
    validateConnectionContext,
    validateResolvedTenantContext
  });

  globalScope.WorkbenchTenantContext = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : self);
