(() => {
  // 多账号并行时，工作台用 ?wh_instance=<tag> 打开每个账号专属浏览器；
  // 这里把实例标识回传给后台，后台随插件会话上报，工作台据此把账号和浏览器对上。
  try {
    const search = new URLSearchParams(location.search);
    let tag = search.get("wh_instance") || "";
    if (!tag) {
      const hash = String(location.hash || "").replace(/^#/, "");
      const params = new URLSearchParams(hash);
      tag = params.get("wh_instance") || "";
      if (!tag) {
        const match = hash.match(/wh-instance=([^&]+)/);
        if (match) tag = decodeURIComponent(match[1]);
      }
    }
    if (!tag) return;
    chrome.runtime.sendMessage({ type: "WH_INSTANCE_TAG", tag }, () => {
      void chrome.runtime.lastError;
    });
  } catch (_error) {
    // 页面上下文异常时静默忽略，后台还有标签页扫描兜底。
  }
})();
