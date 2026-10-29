(function () {
  "use strict";

  var TOKEN_KEY = "mainpg_team_token";
  var ACCOUNT_KEY = "mainpg_team_account";
  var HIDDEN_STORES_KEY = "mainpg_team_hidden_stores";
  var API = location.origin + "/team-api";
  var AUTH = location.origin + "/auth-api";

  var state = {
    token: localStorage.getItem(TOKEN_KEY) || "",
    account: readAccount(),
    range: "7d",
    hiddenStores: readHiddenStores(),
    allStores: [],
    page: "overview",
    orderStatus: "",
    orderKeyword: "",
    trendMode: "store",
    fluxMode: "all",
    fluxStore: "",
    trends: null,
    paging: {
      orders: { page: 1, size: 50 },
      finance: { page: 1, size: 50 },
      traffic: { page: 1, size: 50 },
      sales: { page: 1, size: 50 },
      settlement: { page: 1, size: 50 },
    },
    rows: { orders: [], finance: [], traffic: [], sales: [], settlement: [], metrics: [] },
    loading: false,
  };

  function readHiddenStores() {
    try {
      var list = JSON.parse(localStorage.getItem(HIDDEN_STORES_KEY) || "[]");
      return Array.isArray(list) ? list.map(String) : [];
    } catch (error) {
      return [];
    }
  }

  function saveHiddenStores() {
    localStorage.setItem(HIDDEN_STORES_KEY, JSON.stringify(state.hiddenStores));
  }

  function $(selector) {
    return document.querySelector(selector);
  }

  function readAccount() {
    try {
      return JSON.parse(localStorage.getItem(ACCOUNT_KEY) || "null");
    } catch (error) {
      return null;
    }
  }

  function setView(view) {
    var app = $("#app");
    if (app) app.setAttribute("data-view", view);
  }

  function toast(message) {
    var el = $("#toast");
    if (!el) return;
    el.textContent = message;
    el.classList.add("on");
    window.clearTimeout(toast._timer);
    toast._timer = window.setTimeout(function () {
      el.classList.remove("on");
    }, 2400);
  }

  function money(value) {
    var number = Number(value || 0);
    return number.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function integer(value) {
    return Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 0 });
  }

  // ---------- 日期 ----------
  function iso(date) {
    var year = date.getFullYear();
    var month = String(date.getMonth() + 1).padStart(2, "0");
    var day = String(date.getDate()).padStart(2, "0");
    return year + "-" + month + "-" + day;
  }

  function addDays(date, days) {
    var next = new Date(date.getTime());
    next.setDate(next.getDate() + days);
    return next;
  }

  function rangeDates(kind) {
    var today = new Date();
    if (kind === "all") return { start: "", end: "" };
    if (kind === "week") {
      var offset = (today.getDay() + 6) % 7; // 周一为一周起点
      return { start: iso(addDays(today, -offset)), end: iso(today) };
    }
    if (kind === "30d") return { start: iso(addDays(today, -29)), end: iso(today) };
    return { start: iso(addDays(today, -6)), end: iso(today) };
  }

  // ---------- 请求 ----------
  async function api(path, options) {
    var init = options || {};
    init.headers = Object.assign(
      { Authorization: "Bearer " + state.token },
      init.headers || {}
    );
    var response = await fetch(API + path, init);
    if (response.status === 401) {
      logout(true);
      throw new Error("登录已过期，请重新登录");
    }
    var payload = await response.json().catch(function () {
      return {};
    });
    if (!response.ok) {
      var detail = payload.detail;
      throw new Error(typeof detail === "string" ? detail : "请求失败 " + response.status);
    }
    return payload;
  }

  async function login(event) {
    event.preventDefault();
    var username = $("#loginUsername").value.trim();
    var password = $("#loginPassword").value;
    var error = $("#loginError");
    var button = $("#loginBtn");
    if (!username || !password) {
      error.textContent = "请填写账号和密码";
      return;
    }
    error.textContent = "";
    button.disabled = true;
    button.textContent = "登录中…";
    try {
      var response = await fetch(AUTH + "/api/customer/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ username: username, password: password }),
      });
      var payload = await response.json().catch(function () {
        return {};
      });
      if (!response.ok || !payload.token) {
        throw new Error(
          typeof payload.detail === "string" ? payload.detail : "账号或密码不正确"
        );
      }
      state.token = payload.token;
      state.account = payload.account || { username: username };
      localStorage.setItem(TOKEN_KEY, state.token);
      localStorage.setItem(ACCOUNT_KEY, JSON.stringify(state.account));
      setView("dashboard");
      setPage("overview");
      await load();
    } catch (cause) {
      error.textContent = cause.message || String(cause);
    } finally {
      button.disabled = false;
      button.textContent = "登录";
    }
  }

  function logout(silent) {
    state.token = "";
    state.account = null;
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(ACCOUNT_KEY);
    setView("auth");
    if (!silent) toast("已退出");
  }

  // ---------- 渲染 ----------
  function countUp(element, target, formatter) {
    if (!element) return;
    var from = Number(element.getAttribute("data-value") || 0);
    var to = Number(target || 0);
    element.setAttribute("data-value", String(to));
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      element.textContent = formatter(to);
      return;
    }
    var started = performance.now();
    var duration = 650;
    function frame(now) {
      var progress = Math.min(1, (now - started) / duration);
      var eased = 1 - Math.pow(1 - progress, 3);
      element.textContent = formatter(from + (to - from) * eased);
      if (progress < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  function render(data) {
    var totals = data.totals || {};
    var scope = data.scope || {};
    var accountCount = (scope.visible_account_ids || []).length;
    var allStores = data.all_stores || data.stores || [];
    var storeCount = (data.stores || []).length;

    var who = (state.account && state.account.username) || "";
    var accountId = scope.account_id || "";
    $("#who").textContent = accountId ? who + " · " + accountId : who;
    $("#who").title = accountId
      ? "把这个账号 ID 交给管理员，即可被加入组长组"
      : "";
    $("#scopeLabel").textContent = scope.is_leader
      ? "团队数据 · " + accountCount + " 个账号"
      : "我的店铺";
    $("#rangeLabel").textContent = state.range === "all"
      ? (data.start ? "全部日期 · " + data.start + " ~ " + data.end : "全部日期（暂无数据）")
      : data.start + " ~ " + data.end;
    countUp($("#totalGmv"), totals.gmv_cny || 0, money);

    countUp($("#kpiOrders"), totals.order_count || 0, integer);
    $("#kpiOrdersSub").textContent = "子订单 " + integer(totals.sub_order_count);
    countUp($("#kpiItems"), totals.sub_order_count || 0, integer);
    $("#kpiItemsSub").textContent = "客单件 " + (
      totals.order_count ? (Number(totals.sub_order_count) / Number(totals.order_count)).toFixed(2) : "0.00"
    );
    countUp($("#kpiPayback"), totals.settlement_payback_cny || 0, money);
    $("#kpiPaybackSub").textContent = "结算回款合计";
    countUp($("#kpiExpense"), totals.finance_expense_cny || 0, money);
    $("#kpiExpenseSub").textContent = "对账中心支出";
    countUp($("#kpiAov"), totals.avg_order_value || 0, money);

    $("#storeCountPill").textContent = storeCount === allStores.length
      ? storeCount + " 家店铺"
      : storeCount + " / " + allStores.length + " 家店铺";
    $("#accountCountPill").textContent = accountCount + " 个账号";
    syncStoreFilter(data, storeCount);
    var flagTotal = (data.stores || []).reduce(function (sum, item) {
      return sum + Number(item.flag_count || 0);
    }, 0);
    var flagPill = $("#flagPill");
    if (flagTotal > 0) {
      flagPill.hidden = false;
      flagPill.textContent = flagTotal + " 条查重标记";
    } else {
      flagPill.hidden = true;
    }

    renderRanks(data.top_stores || []);
    renderTable(data.stores || [], data.top_stores || []);
  }

  /** 店铺筛选：取消勾选的店铺会被剔除出统计（前端记住，接口按 exclude 过滤）。 */
  function renderStoreFilter(visibleCount, allStores) {
    var count = $("#storeFilterCount");
    if (count) count.textContent = visibleCount + "/" + allStores.length;
    var el = $("#storeFilterPop");
    if (!el) return;
    el.innerHTML = "";

    var head = document.createElement("div");
    head.className = "sf-head";
    var title = document.createElement("span");
    title.textContent = "只统计勾选的店铺";
    head.appendChild(title);
    if (state.hiddenStores.length) {
      var reset = document.createElement("button");
      reset.type = "button";
      reset.className = "sf-all";
      reset.textContent = "全部显示";
      reset.addEventListener("click", function () {
        state.hiddenStores = [];
        saveHiddenStores();
        load();
      });
      head.appendChild(reset);
    }
    el.appendChild(head);

    if (!allStores.length) {
      var empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "还没有店铺";
      el.appendChild(empty);
      return;
    }

    allStores.forEach(function (store) {
      var code = String(store.store_code || "");
      if (!code) return;
      var row = document.createElement("label");
      var box = document.createElement("input");
      box.type = "checkbox";
      box.checked = state.hiddenStores.indexOf(code) === -1;
      box.addEventListener("change", function () {
        var index = state.hiddenStores.indexOf(code);
        if (box.checked && index !== -1) state.hiddenStores.splice(index, 1);
        if (!box.checked && index === -1) state.hiddenStores.push(code);
        saveHiddenStores();
        load();
      });
      row.appendChild(box);

      var name = document.createElement("span");
      name.className = "sf-name";
      name.textContent = store.store_name || code;
      row.appendChild(name);

      var codeEl = document.createElement("span");
      codeEl.className = "sf-code";
      codeEl.textContent = code;
      row.appendChild(codeEl);
      el.appendChild(row);
    });
  }

  var CHART_COLORS = ["#10b981", "#3b82f6", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6", "#ef4444", "#6366f1"];

  function compact(value) {
    var number = Number(value || 0);
    if (Math.abs(number) >= 10000) return (number / 10000).toFixed(1) + "万";
    return integer(number);
  }

  function smoothPath(points) {
    if (!points.length) return "";
    if (points.length === 1) {
      return "M" + points[0][0].toFixed(1) + " " + points[0][1].toFixed(1);
    }
    var d = "M" + points[0][0].toFixed(1) + " " + points[0][1].toFixed(1);
    for (var i = 0; i < points.length - 1; i += 1) {
      var p0 = points[i];
      var p1 = points[i + 1];
      var dx = (p1[0] - p0[0]) / 2.6;
      d += " C" + (p0[0] + dx).toFixed(1) + " " + p0[1].toFixed(1) +
        ", " + (p1[0] - dx).toFixed(1) + " " + p1[1].toFixed(1) +
        ", " + p1[0].toFixed(1) + " " + p1[1].toFixed(1);
    }
    return d;
  }

  /** 多店铺曲线面积图：每条线一个颜色，曲线平滑，线下填充渐变。
      options.stacked = true 时按「渠道构成」画堆叠面积。 */
  function drawChart(el, dates, series, key, formatter, options) {
    if (!el) return;
    el.innerHTML = "";
    if (!dates.length || !series.length) {
      el.innerHTML = '<div class="empty"><strong>这段时间没有数据</strong><p>员工提交周汇总后这里会显示趋势。</p></div>';
      return;
    }
    var stacked = Boolean(options && options.stacked);
    var width = el.clientWidth || 640;
    var height = el.clientHeight || 200;
    var pad = { left: 52, right: 14, top: 14, bottom: 24 };
    var max = 1;
    if (stacked) {
      dates.forEach(function (_, pointIndex) {
        var total = 0;
        series.forEach(function (item) {
          total += Number((item[key] || [])[pointIndex] || 0);
        });
        max = Math.max(max, total);
      });
    } else {
      series.forEach(function (item) {
        (item[key] || []).forEach(function (value) {
          max = Math.max(max, Number(value || 0));
        });
      });
    }
    var uid = "g" + Math.random().toString(36).slice(2, 7);

    function xAt(index) {
      if (dates.length === 1) return (pad.left + width - pad.right) / 2;
      return pad.left + ((width - pad.left - pad.right) * index) / (dates.length - 1);
    }
    function yAt(value) {
      return pad.top + (height - pad.top - pad.bottom) * (1 - Number(value || 0) / max);
    }

    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("preserveAspectRatio", "none");

    var defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    series.forEach(function (item, index) {
      var gradient = document.createElementNS("http://www.w3.org/2000/svg", "linearGradient");
      gradient.setAttribute("id", uid + "-" + index);
      gradient.setAttribute("x1", "0");
      gradient.setAttribute("y1", "0");
      gradient.setAttribute("x2", "0");
      gradient.setAttribute("y2", "1");
      var top = document.createElementNS("http://www.w3.org/2000/svg", "stop");
      top.setAttribute("offset", "0%");
      top.setAttribute("stop-color", item.color);
      top.setAttribute("stop-opacity", "0.26");
      var bottom = document.createElementNS("http://www.w3.org/2000/svg", "stop");
      bottom.setAttribute("offset", "100%");
      bottom.setAttribute("stop-color", item.color);
      bottom.setAttribute("stop-opacity", "0");
      gradient.appendChild(top);
      gradient.appendChild(bottom);
      defs.appendChild(gradient);
    });
    svg.appendChild(defs);

    var grid = document.createElementNS("http://www.w3.org/2000/svg", "g");
    grid.setAttribute("class", "grid");
    for (var line = 0; line <= 3; line += 1) {
      var y = pad.top + ((height - pad.top - pad.bottom) * line) / 3;
      var rule = document.createElementNS("http://www.w3.org/2000/svg", "line");
      rule.setAttribute("x1", pad.left);
      rule.setAttribute("x2", width - pad.right);
      rule.setAttribute("y1", y);
      rule.setAttribute("y2", y);
      grid.appendChild(rule);
      var label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("x", pad.left - 10);
      label.setAttribute("y", y + 4);
      label.setAttribute("text-anchor", "end");
      label.textContent = formatter(max * (1 - line / 3));
      grid.appendChild(label);
    }
    svg.appendChild(grid);

    var stackedBase = dates.map(function () { return 0; });
    series.forEach(function (item, index) {
      var values = item[key] || [];
      var points = values.map(function (value, pointIndex) {
        var base = stacked ? stackedBase[pointIndex] : 0;
        return [xAt(pointIndex), yAt(base + Number(value || 0))];
      });
      var linePath = smoothPath(points);
      var areaPath = linePath;
      if (stacked) {
        for (var baseIndex = points.length - 1; baseIndex >= 0; baseIndex -= 1) {
          areaPath += " L" + points[baseIndex][0].toFixed(1) + " " + yAt(stackedBase[baseIndex]).toFixed(1);
        }
      } else {
        areaPath += " L" + points[points.length - 1][0].toFixed(1) + " " +
          yAt(0).toFixed(1) + " L" + points[0][0].toFixed(1) + " " + yAt(0).toFixed(1);
      }
      areaPath += " Z";
      if (stacked) {
        values.forEach(function (value, pointIndex) {
          stackedBase[pointIndex] += Number(value || 0);
        });
      }

      var area = document.createElementNS("http://www.w3.org/2000/svg", "path");
      area.setAttribute("d", areaPath);
      area.setAttribute("fill", "url(#" + uid + "-" + index + ")");
      area.setAttribute("stroke", "none");
      svg.appendChild(area);

      var stroke = document.createElementNS("http://www.w3.org/2000/svg", "path");
      stroke.setAttribute("class", "series-line");
      stroke.setAttribute("d", linePath);
      stroke.setAttribute("stroke", item.color);
      svg.appendChild(stroke);

      if (dates.length <= 40) {
        points.forEach(function (point) {
          var dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
          dot.setAttribute("class", "series-dot");
          dot.setAttribute("cx", point[0]);
          dot.setAttribute("cy", point[1]);
          dot.setAttribute("r", 3);
          dot.setAttribute("fill", "var(--surface)");
          dot.setAttribute("stroke", item.color);
          svg.appendChild(dot);
        });
      }
    });

    var axis = document.createElementNS("http://www.w3.org/2000/svg", "g");
    axis.setAttribute("class", "axis");
    var every = Math.max(1, Math.ceil(dates.length / 7));
    dates.forEach(function (day, index) {
      if (index % every !== 0 && index !== dates.length - 1) return;
      var text = document.createElementNS("http://www.w3.org/2000/svg", "text");
      text.setAttribute("x", xAt(index));
      text.setAttribute("y", height - 6);
      text.setAttribute("text-anchor", "middle");
      text.textContent = String(day || "").slice(5);
      axis.appendChild(text);
    });
    svg.appendChild(axis);
    el.appendChild(svg);
  }

  var chartState = { scale: "day", start: 0, end: 0, dragging: null };
  var chartData = { day: null, month: null };

  /** 把按日的点聚合成按月，用于缩到「月」粒度。 */
  function buildMonthly(data) {
    var dates = [];
    var indexOf = {};
    (data.dates || []).forEach(function (day) {
      var key = String(day || "").slice(0, 7);
      if (!(key in indexOf)) {
        indexOf[key] = dates.length;
        dates.push(key);
      }
    });
    function blank() {
      return dates.map(function () { return { order_count: 0, gmv_cny: 0 }; });
    }
    var series = (data.series || []).map(function (item) {
      var points = blank();
      (item.points || []).forEach(function (point, index) {
        var key = String((data.dates || [])[index] || "").slice(0, 7);
        var target = indexOf[key];
        if (target == null) return;
        points[target].order_count += Number(point.order_count || 0);
        points[target].gmv_cny += Number(point.gmv_cny || 0);
      });
      return { store_code: item.store_code, store_name: item.store_name, points: points };
    });
    var totals = blank();
    (data.totals || []).forEach(function (point, index) {
      var key = String((data.dates || [])[index] || "").slice(0, 7);
      var target = indexOf[key];
      if (target == null) return;
      totals[target].order_count += Number(point.order_count || 0);
      totals[target].gmv_cny += Number(point.gmv_cny || 0);
    });
    return { dates: dates, series: series, totals: totals, series_total: data.series_total };
  }

  function renderTrendLegend(series, total) {
    var el = $("#trendLegend");
    if (!el) return;
    el.innerHTML = "";
    series.forEach(function (item) {
      var span = document.createElement("span");
      span.className = "item";
      span.innerHTML = "<i class='dot' style='background:" + item.color + "'></i>" +
        escapeHtml(item.name);
      el.appendChild(span);
    });
    if (total && total > series.length) {
      var more = document.createElement("span");
      more.className = "item";
      more.textContent = "另有 " + (total - series.length) + " 家店铺未显示（按 GMV 取前 " + series.length + "）";
      el.appendChild(more);
    }
  }

  function chartDataset() {
    return chartData[chartState.scale] || { dates: [], series: [], totals: [], series_total: 0 };
  }

  function resetChartWindow() {
    chartState.start = 0;
    chartState.end = Math.max(0, chartDataset().dates.length - 1);
  }

  function renderCharts() {
    var data = chartDataset();
    var total = data.dates.length;
    if (!total) {
      drawChart($("#trendOrders"), [], [], "values", compact);
      drawChart($("#trendAmount"), [], [], "amount", function (value) { return "¥" + compact(value); });
      renderTrendLegend([], 0);
      if ($("#trendWindow")) $("#trendWindow").textContent = "暂无数据";
      return;
    }
    var start = Math.max(0, Math.min(chartState.start, total - 1));
    var end = Math.max(start, Math.min(chartState.end, total - 1));
    chartState.start = start;
    chartState.end = end;
    var dates = data.dates.slice(start, end + 1);
    var series = [];
    if (state.trendMode === "total") {
      series = [{
        name: "总计",
        color: CHART_COLORS[0],
        values: data.totals.slice(start, end + 1).map(function (row) { return row.order_count; }),
        amount: data.totals.slice(start, end + 1).map(function (row) { return row.gmv_cny; }),
      }];
    } else {
      series = (data.series || []).map(function (item, index) {
        return {
          name: item.store_name || item.store_code,
          color: CHART_COLORS[index % CHART_COLORS.length],
          values: (item.points || []).slice(start, end + 1).map(function (point) {
            return point.order_count;
          }),
          amount: (item.points || []).slice(start, end + 1).map(function (point) {
            return point.gmv_cny;
          }),
        };
      });
    }
    drawChart($("#trendOrders"), dates, series, "values", compact);
    drawChart($("#trendAmount"), dates, series, "amount", function (value) {
      return "¥" + compact(value);
    });
    renderTrendLegend(series, state.trendMode === "total" ? 0 : data.series_total);
    if ($("#trendWindow")) {
      $("#trendWindow").textContent = dates[0] + " ~ " + dates[dates.length - 1] +
        " · " + (chartState.scale === "month" ? "按月" : "按日") +
        (dates.length < total ? " · 已放大 " + dates.length + "/" + total : "");
    }
  }

  function renderTrends(data) {
    state.trends = data;
    chartData.day = {
      dates: data.dates || [],
      series: data.series || [],
      totals: data.totals || [],
      series_total: data.series_total,
    };
    chartData.month = buildMonthly(data);
    var defaultScale = (data.dates || []).length > 62 ? "month" : "day";
    chartState.scale = defaultScale;
    document.querySelectorAll("#trendScale button").forEach(function (button) {
      button.classList.toggle("active", button.getAttribute("data-scale") === defaultScale);
    });
    resetChartWindow();
    renderCharts();
  }

  function chartSpanTotal() {
    return chartDataset().dates.length;
  }

  function zoomChart(direction, clientX, el) {
    var total = chartSpanTotal();
    if (total < 3) return;
    var rect = el.getBoundingClientRect();
    var ratio = Math.min(1, Math.max(0, (clientX - rect.left) / Math.max(1, rect.width)));
    var span = chartState.end - chartState.start + 1;
    var focus = chartState.start + ratio * Math.max(0, span - 1);
    var nextSpan = Math.round(span * (direction > 0 ? 1.35 : 0.74));
    nextSpan = Math.max(3, Math.min(total, nextSpan));
    var nextStart = Math.round(focus - ratio * (nextSpan - 1));
    nextStart = Math.max(0, Math.min(total - nextSpan, nextStart));
    chartState.start = nextStart;
    chartState.end = nextStart + nextSpan - 1;
    renderCharts();
  }

  function panChart(deltaX, el) {
    var total = chartSpanTotal();
    var span = chartState.end - chartState.start + 1;
    if (span >= total) return;
    var rect = el.getBoundingClientRect();
    var shift = Math.round((deltaX / Math.max(1, rect.width)) * span);
    if (!shift) return;
    var nextStart = Math.max(0, Math.min(total - span, chartState.start - shift));
    if (nextStart === chartState.start) return;
    chartState.start = nextStart;
    chartState.end = nextStart + span - 1;
    renderCharts();
  }

  function renderRanks(rows) {
    var el = $("#rankList");
    if (!el) return;
    el.innerHTML = "";
    if (!rows.length) {
      el.innerHTML = '<p class="muted">还没有店铺数据</p>';
      return;
    }
    var max = Math.max.apply(
      null,
      rows.map(function (row) {
        return Number(row.gmv_cny || 0);
      }).concat([1])
    );
    rows.slice(0, 6).forEach(function (row, index) {
      var item = document.createElement("div");
      item.className = "rank";
      item.innerHTML =
        "<div class='rank-top'><span>" + escapeHtml(row.label || row.key || "-") +
        "</span><em>¥" + money(row.gmv_cny) + "</em></div>" +
        "<div class='rank-bar'><i></i></div>";
      el.appendChild(item);
      var bar = item.querySelector("i");
      var ratio = Math.max(2, (Number(row.gmv_cny || 0) / max) * 100);
      window.setTimeout(function () {
        bar.style.width = ratio + "%";
      }, 60 + index * 60);
    });
  }

  function renderTable(stores, ranking) {
    var body = $("#storeRows");
    var empty = $("#emptyState");
    if (!body) return;
    body.innerHTML = "";
    var gmvByStore = {};
    ranking.forEach(function (row) {
      gmvByStore[row.key] = row;
    });
    if (!stores.length) {
      empty.hidden = false;
      $("#tableHint").textContent = "";
      return;
    }
    empty.hidden = true;
    $("#tableHint").textContent = stores.length + " 家店铺";
    stores.forEach(function (store) {
      var stat = gmvByStore[store.store_code] || {};
      var flags = Number(store.flag_count || 0);
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td class='store-name'>" + escapeHtml(store.store_name || "-") + "</td>" +
        "<td class='code'>" + escapeHtml(store.store_code || "-") + "</td>" +
        "<td>" + escapeHtml(store.staff_no || store.username || "-") + "</td>" +
        "<td class='num'>¥" + money(stat.gmv_cny) + "</td>" +
        "<td class='num'>" + integer(stat.order_count) + "</td>" +
        "<td class='num'>¥" + money(stat.settlement_payback_cny) + "</td>" +
        "<td class='num'>¥" + money(stat.finance_expense_cny) + "</td>" +
        "<td>" + escapeHtml(store.last_stat_date || "未上报") + "</td>" +
        "<td class='num'>" + (flags > 0 ? "<span class='badge warn'>" + flags + "</span>" : "<span class='badge'>0</span>") + "</td>";
      body.appendChild(tr);
    });
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function syncStoreFilter(data, visibleCount) {
    var all = data.all_stores || data.stores || [];
    state.allStores = all;
    // 筛选里存着已经不存在的店铺码时先清掉，避免一直带着无效 exclude。
    var known = {};
    all.forEach(function (item) {
      known[String(item.store_code || "")] = true;
    });
    var pruned = state.hiddenStores.filter(function (code) {
      return known[code];
    });
    if (pruned.length !== state.hiddenStores.length) {
      state.hiddenStores = pruned;
      saveHiddenStores();
    }
    renderStoreFilter(
      visibleCount == null ? all.length : visibleCount,
      all
    );
  }

  function rangeParams(extra) {
    var dates = rangeDates(state.range);
    var parts = [];
    if (state.range === "all") parts.push("all=1");
    else parts.push("from=" + dates.start, "to=" + dates.end);
    if (state.hiddenStores.length) {
      parts.push("exclude=" + encodeURIComponent(state.hiddenStores.join(",")));
    }
    return parts.concat(extra || []).join("&");
  }

  function storeNames(data) {
    var map = {};
    (data.all_stores || data.stores || []).forEach(function (item) {
      map[String(item.store_code || "")] = item.store_name || item.store_code;
    });
    return map;
  }

  function sumCard(label, value) {
    return "<div class='sum-card'><span>" + label + "</span><strong>" + value + "</strong></div>";
  }

  function shortTime(value) {
    return String(value || "").replace("T", " ").slice(0, 16) || "-";
  }

  function statusBadge(status) {
    var text = String(status || "-");
    var warn = /取消|关闭/.test(text) ? " warn" : "";
    return "<span class='badge" + warn + "'>" + escapeHtml(text) + "</span>";
  }

  function formatMetrics(metrics) {
    if (!metrics || typeof metrics !== "object") return "-";
    var keys = Object.keys(metrics).slice(0, 6);
    if (!keys.length) return "-";
    return keys
      .map(function (key) {
        var value = metrics[key];
        if (value && typeof value === "object") value = JSON.stringify(value);
        return key + " " + value;
      })
      .join(" · ");
  }

  function setPage(page) {
    state.page = page;
    document.querySelectorAll(".nav-item").forEach(function (item) {
      item.classList.toggle("active", item.getAttribute("data-page") === page);
    });
    document.querySelectorAll(".page").forEach(function (section) {
      section.hidden = section.getAttribute("data-page") !== page;
    });
    var whoSide = $("#whoSide");
    if (whoSide) whoSide.textContent = (state.account && state.account.username) || "—";
  }

  function pageSlice(kind, rows) {
    var cfg = state.paging[kind] || { page: 1, size: 50 };
    var size = Math.max(10, Number(cfg.size) || 50);
    var totalPages = Math.max(1, Math.ceil(rows.length / size));
    var page = Math.min(Math.max(1, Number(cfg.page) || 1), totalPages);
    state.paging[kind] = { page: page, size: size };
    return {
      rows: rows.slice((page - 1) * size, page * size),
      page: page,
      size: size,
      totalPages: totalPages,
      total: rows.length,
    };
  }

  function resetPaging(kind) {
    if (state.paging[kind]) state.paging[kind].page = 1;
  }

  /** 表格分页条：首页/上一页/下一页/末页 + 每页条数。 */
  function renderPager(selector, kind, info, repaint) {
    var el = $(selector);
    if (!el) return;
    if (!info.total) {
      el.innerHTML = "";
      el.hidden = true;
      return;
    }
    el.hidden = false;
    var options = [20, 50, 100].map(function (size) {
      return "<option value='" + size + "'" + (size === info.size ? " selected" : "") + ">" +
        size + " 条/页</option>";
    }).join("");
    var atFirst = info.page <= 1;
    var atLast = info.page >= info.totalPages;
    el.innerHTML =
      "<span class='muted'>共 " + integer(info.total) + " 条 · 第 " + info.page + "/" +
        info.totalPages + " 页</span>" +
      "<div class='pager-actions'>" +
        "<button type='button' class='chip ghost' data-page='1'" + (atFirst ? " disabled" : "") + ">首页</button>" +
        "<button type='button' class='chip ghost' data-page='" + (info.page - 1) + "'" + (atFirst ? " disabled" : "") + ">上一页</button>" +
        "<button type='button' class='chip ghost' data-page='" + (info.page + 1) + "'" + (atLast ? " disabled" : "") + ">下一页</button>" +
        "<button type='button' class='chip ghost' data-page='" + info.totalPages + "'" + (atLast ? " disabled" : "") + ">末页</button>" +
        "<select class='pager-size'>" + options + "</select>" +
      "</div>";
    el.querySelectorAll("[data-page]").forEach(function (button) {
      button.addEventListener("click", function () {
        state.paging[kind].page = Number(button.getAttribute("data-page")) || 1;
        repaint();
      });
    });
    var select = el.querySelector(".pager-size");
    if (select) {
      select.addEventListener("change", function () {
        state.paging[kind] = { page: 1, size: Number(select.value) || 50 };
        repaint();
      });
    }
  }

  function renderOrders(data) {
    state.rows.orders = data.records || [];
    state.storeNames = storeNames(data);
    resetPaging("orders");
    paintOrders();
    syncStoreFilter(data, (data.stores || []).length);
  }

  function paintOrders() {
    var rows = state.rows.orders;
    var names = state.storeNames || {};
    var body = $("#orderRows");
    body.innerHTML = "";
    $("#ordersEmpty").hidden = rows.length > 0;
    $("#ordersHint").textContent = rows.length + " 条明细";
    var parents = {};
    var subCount = 0;
    var itemCount = 0;
    var gmv = 0;
    var payback = 0;
    var profit = 0;
    rows.forEach(function (row) {
      var p = row.payload || {};
      parents[String(p.parent_order_sn || row.key)] = true;
      subCount += 1;
      itemCount += Number(p.quantity || 0);
      gmv += Number(p.gmv_cny || 0);
      payback += Number(p.sales_payback || 0);
      profit += Number(p.profit_estimate_cny || 0);
    });
    var info = pageSlice("orders", rows);
    info.rows.forEach(function (row) {
      var p = row.payload || {};
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td class='store-name'>" + escapeHtml(names[row.store_code] || row.store_code) + "</td>" +
        "<td class='code'>" + escapeHtml(p.parent_order_sn || "-") + "</td>" +
        "<td>" + escapeHtml(p.site || "-") + "</td>" +
        "<td>" + statusBadge(p.order_status) + "</td>" +
        "<td>" + escapeHtml(shortTime(p.order_time)) + "</td>" +
        "<td class='num'>" + escapeHtml((p.sku_id || "-") + " / " + integer(p.quantity)) + "</td>" +
        "<td class='num'>¥" + money(p.gmv_cny) + "</td>" +
        "<td class='num'>¥" + money(p.sales_payback) + "</td>" +
        "<td class='num neg'>¥" + money(p.sales_reversal) + "</td>" +
        "<td class='num'>¥" + money(p.purchase_cost_cny) + "</td>" +
        "<td class='num'>¥" + money(p.head_freight_cny) + "</td>" +
        "<td class='num'>¥" + money(p.tail_freight_cny) + "</td>" +
        "<td class='num " + (Number(p.profit_estimate_cny || 0) < 0 ? "neg" : "pos") + "'>¥" +
        money(p.profit_estimate_cny) + "</td>";
      body.appendChild(tr);
    });
    $("#orderSums").innerHTML =
      sumCard("订单数", integer(Object.keys(parents).length)) +
      sumCard("子订单", integer(subCount)) +
      sumCard("件数", integer(itemCount)) +
      sumCard("GMV", "¥" + money(gmv)) +
      sumCard("销售回款", "¥" + money(payback)) +
      sumCard("预估利润", "¥" + money(profit));
    renderPager("#orderPager", "orders", info, paintOrders);
  }

  function renderFinance(data) {
    state.rows.finance = data.records || [];
    state.storeNames = storeNames(data);
    resetPaging("finance");
    paintFinance();
    syncStoreFilter(data, (data.stores || []).length);
  }

  function paintFinance() {
    var rows = state.rows.finance;
    var names = state.storeNames || {};
    var body = $("#financeRows");
    body.innerHTML = "";
    $("#financeEmpty").hidden = rows.length > 0;
    $("#financeHint").textContent = rows.length + " 条流水";
    var total = 0;
    var biggest = 0;
    rows.forEach(function (row) {
      var amount = Number((row.payload || {}).amount || 0);
      total += Math.abs(amount);
      biggest = Math.max(biggest, Math.abs(amount));
    });
    var info = pageSlice("finance", rows);
    info.rows.forEach(function (row) {
      var p = row.payload || {};
      var amount = Number(p.amount || 0);
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td class='code'>" + escapeHtml(shortTime(p.entry_time)) + "</td>" +
        "<td class='store-name'>" + escapeHtml(names[row.store_code] || row.store_code) + "</td>" +
        "<td>" + escapeHtml(p.entry_type || "-") + "</td>" +
        "<td>" + escapeHtml(p.category || "-") + "</td>" +
        "<td class='num'>" + escapeHtml(String(p.currency || "")) + " " + money(amount) + "</td>" +
        "<td>" + escapeHtml(p.currency || "-") + "</td>" +
        "<td class='sub'>" + escapeHtml(p.note || "") + "</td>";
      body.appendChild(tr);
    });
    $("#financeSums").innerHTML =
      sumCard("流水笔数", integer(rows.length)) +
      sumCard("金额合计", "¥" + money(total)) +
      sumCard("最大单笔", "¥" + money(biggest));
    renderPager("#financePager", "finance", info, paintFinance);
  }

  function renderTraffic(data) {
    var rows = data.records || [];
    state.rows.traffic = rows.filter(function (row) { return row.kind === "traffic"; });
    state.rows.sales = rows.filter(function (row) { return row.kind === "sales"; });
    state.storeNames = storeNames(data);
    resetPaging("traffic");
    resetPaging("sales");
    paintTraffic();
    renderFlux();
    syncStoreFilter(data, (data.stores || []).length);
  }

  function paintTraffic() {
    var names = state.storeNames || {};

    var trafficRows = state.rows.traffic;
    var trafficBody = $("#trafficRows");
    trafficBody.innerHTML = "";
    $("#trafficEmpty").hidden = trafficRows.length > 0;
    $("#trafficHint").textContent = trafficRows.length + " 条快照";
    var trafficInfo = pageSlice("traffic", trafficRows);
    trafficInfo.rows.forEach(function (row) {
      var p = row.payload || {};
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td class='code'>" + escapeHtml(row.stat_date || "-") + "</td>" +
        "<td class='store-name'>" + escapeHtml(names[row.store_code] || row.store_code) + "</td>" +
        "<td>" + escapeHtml((p.scope || "-") + (p.period ? " · " + p.period : "")) + "</td>" +
        "<td class='num'>" + integer(p.exposure) + "</td>" +
        "<td class='num'>" + integer(p.clicks) + "</td>" +
        "<td class='num'>" + integer(p.visitors) + "</td>" +
        "<td class='num'>" + escapeHtml(p.click_rate || "-") + "</td>" +
        "<td class='num'>" + integer(p.paid_orders) + "</td>";
      trafficBody.appendChild(tr);
    });
    renderPager("#trafficPager", "traffic", trafficInfo, paintTraffic);

    var salesRows = state.rows.sales;
    var salesBody = $("#salesRows");
    salesBody.innerHTML = "";
    $("#salesEmpty").hidden = salesRows.length > 0;
    $("#salesHint").textContent = salesRows.length + " 条快照";
    var salesInfo = pageSlice("sales", salesRows);
    salesInfo.rows.forEach(function (row) {
      var p = row.payload || {};
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td class='code'>" + escapeHtml(row.stat_date || "-") + "</td>" +
        "<td class='store-name'>" + escapeHtml(names[row.store_code] || row.store_code) + "</td>" +
        "<td>" + escapeHtml(p.region || "-") + "</td>" +
        "<td class='sub'>" + escapeHtml(formatMetrics(p.metrics)) + "</td>";
      salesBody.appendChild(tr);
    });
    renderPager("#salesPager", "sales", salesInfo, paintTraffic);
  }

  // ---------- 店铺指标（分类 + 汇总） ----------
  function renderMetrics(data) {
    state.rows.metrics = data.records || [];
    state.storeNames = storeNames(data);
    state.storeStaff = staffMap(data);
    paintMetrics();
    syncStoreFilter(data, (data.stores || []).length);
  }

  function staffMap(data) {
    var map = {};
    (data.all_stores || data.stores || []).forEach(function (item) {
      map[String(item.store_code || "")] = item.staff_no || item.username || "";
    });
    return map;
  }

  /** 每家店只取最新一条指标快照，避免同一店铺重复计数。 */
  function latestMetricByStore() {
    var map = {};
    state.rows.metrics.forEach(function (row) {
      var payload = row.payload || {};
      var code = String(row.store_code || "");
      if (!code) return;
      var stamp = String(payload.captured_at || row.stat_date || "");
      if (!map[code] || stamp > map[code].stamp) map[code] = { stamp: stamp, payload: payload };
    });
    return map;
  }

  function paintMetrics() {
    var names = state.storeNames || {};
    var staff = state.storeStaff || {};
    var latest = latestMetricByStore();
    var codes = Object.keys(latest).sort();
    var body = $("#metricRows");
    if (!body) return;
    body.innerHTML = "";
    $("#metricsEmpty").hidden = codes.length > 0;
    $("#metricsHint").textContent = codes.length + " 家店铺";
    var pending = 0;
    var active = 0;
    var sales30 = 0;
    codes.forEach(function (code) {
      var payload = latest[code].payload;
      var rowPending = Number(payload.pending_shipment || 0);
      var rowActive = Number(payload.active_products || 0);
      var rowSales = Number(payload.sales_30d || 0);
      pending += rowPending;
      active += rowActive;
      sales30 += rowSales;
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td class='store-name'>" + escapeHtml(names[code] || code) + "</td>" +
        "<td class='code'>" + escapeHtml(code) + "</td>" +
        "<td>" + escapeHtml(staff[code] || "-") + "</td>" +
        "<td class='num'>" + integer(rowPending) + "</td>" +
        "<td class='num'>" + integer(rowActive) + "</td>" +
        "<td class='num'>" + integer(rowSales) + "</td>" +
        "<td class='num " + (Number(payload.refund_pending || 0) > 0 ? "neg" : "") + "'>" +
          integer(payload.refund_pending) + "</td>" +
        "<td class='num " + (Number(payload.cancel_pending || 0) > 0 ? "neg" : "") + "'>" +
          integer(payload.cancel_pending) + "</td>" +
        "<td>" + escapeHtml(shortTime(payload.captured_at)) + "</td>";
      body.appendChild(tr);
    });
    $("#metricSums").innerHTML =
      sumCard("店铺数", integer(codes.length)) +
      sumCard("待发货合计", integer(pending)) +
      sumCard("在售商品合计", integer(active)) +
      sumCard("近 30 日销量合计", integer(sales30));
  }

  /** 采集批次：按最近入库时间倒序展示，模式区分列表/详情。 */
  function renderBatches(data) {
    var rows = (data && data.batches) || [];
    var body = $("#batchRows");
    if (!body) return;
    body.innerHTML = "";
    $("#batchEmpty").hidden = rows.length > 0;
    $("#batchHint").textContent = rows.length + " 次入库";
    rows.forEach(function (row) {
      var mode = row.capture_mode === "order_detail" ? "订单详情" : "订单列表";
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td class='code'>" + escapeHtml(row.batch_id || "-") + "</td>" +
        "<td>" + escapeHtml(mode) + "</td>" +
        "<td class='num'>" + integer(row.order_count) + "</td>" +
        "<td class='num'>" + integer(row.sub_order_count) + "</td>" +
        "<td class='num'>" + integer(row.item_count) + "</td>" +
        "<td>" + escapeHtml(shortTime(row.captured_at)) + "</td>";
      body.appendChild(tr);
    });
  }

  // ---------- 按小时流量（分类 + 汇总） ----------
  var FLUX_COLORS = ["#fb7701", "#486482", "#f9d013", "#10b981", "#8b5cf6"];

  function hourTick(value) {
    var match = String(value || "").match(/(\d{1,2})(?::\d{2})?$/);
    if (!match) return String(value || "");
    var hour = match[1];
    return (hour.length === 1 ? "0" + hour : hour) + ":00";
  }

  function fluxTrafficRows() {
    return (state.rows.traffic || []).filter(function (row) {
      var payload = row.payload || {};
      var trend = payload.metrics && payload.metrics.trend;
      if (payload.scope !== "product" || !trend) return false;
      return Boolean((trend.series || []).length || (trend.channels || []).length);
    });
  }

  function latestFluxByStore() {
    var map = {};
    fluxTrafficRows().forEach(function (row) {
      var payload = row.payload || {};
      var code = String(row.store_code || "");
      if (!code) return;
      var stamp = String(payload.captured_at || row.stat_date || "");
      if (!map[code] || stamp > map[code].stamp) map[code] = { stamp: stamp, trend: payload.metrics.trend };
    });
    return map;
  }

  /** 把多家店的逐小时序列按时段对齐后求和；单店时就是原序列。 */
  function mergeFluxField(trends, field) {
    var labels = [];
    var indexOf = {};
    var values = {};
    var order = [];
    trends.forEach(function (trend) {
      (trend[field] || []).forEach(function (serie) {
        var key = String(serie.key || "");
        if (!key) return;
        if (!(key in values)) {
          values[key] = {};
          order.push(key);
        }
        (serie.points || []).forEach(function (point) {
          var label = hourTick(point.time);
          if (!(label in indexOf)) {
            indexOf[label] = labels.length;
            labels.push(label);
          }
          var slot = indexOf[label];
          values[key][slot] = (values[key][slot] || 0) + Number(point.value || 0);
        });
      });
    });
    return {
      labels: labels,
      series: order.map(function (key, index) {
        return {
          name: key,
          color: FLUX_COLORS[index % FLUX_COLORS.length],
          values: labels.map(function (_, slot) {
            return values[key][slot] || 0;
          }),
        };
      }),
    };
  }

  function fluxEmptyText() {
    return '<div class="empty"><strong>还没有按小时流量</strong><p>员工采集商品流量并提交汇总后，这里会按时段展示曝光趋势与渠道构成。</p></div>';
  }

  function renderFlux() {
    var map = latestFluxByStore();
    var codes = Object.keys(map).sort();
    var names = state.storeNames || {};
    var select = $("#fluxStore");
    if (select) {
      select.hidden = state.fluxMode !== "store";
      var options = codes.map(function (code) {
        return "<option value='" + escapeHtml(code) + "'>" + escapeHtml(names[code] || code) + "</option>";
      }).join("");
      if (select.getAttribute("data-options") !== options) {
        select.innerHTML = options;
        select.setAttribute("data-options", options);
      }
      if (state.fluxStore && codes.indexOf(state.fluxStore) >= 0) select.value = state.fluxStore;
      else if (codes.length) select.value = codes[0];
      state.fluxStore = select.value || "";
    }
    var picked = state.fluxMode === "store"
      ? (state.fluxStore && map[state.fluxStore] ? [map[state.fluxStore].trend] : [])
      : codes.map(function (code) { return map[code].trend; });

    var trend = mergeFluxField(picked, "series");
    var channels = mergeFluxField(picked, "channels");
    var scopeLabel = state.fluxMode === "store"
      ? (names[state.fluxStore] || state.fluxStore || "未选择店铺")
      : codes.length + " 家店铺汇总";

    if ($("#fluxTrendHint")) $("#fluxTrendHint").textContent = trend.labels.length ? scopeLabel + " · 曝光量" : "—";
    if ($("#fluxChannelHint")) $("#fluxChannelHint").textContent = channels.series.length
      ? channels.series.map(function (item) { return item.name; }).join(" / ")
      : "—";

    var trendEl = $("#fluxTrendChart");
    var channelEl = $("#fluxChannelChart");
    if (!trend.labels.length) {
      if (trendEl) trendEl.innerHTML = fluxEmptyText();
      if (channelEl) channelEl.innerHTML = fluxEmptyText();
    } else {
      drawChart(trendEl, trend.labels, trend.series, "values", integer);
      drawChart(channelEl, channels.labels, channels.series, "values", integer, { stacked: true });
    }

    var legend = $("#fluxLegend");
    if (legend) {
      legend.innerHTML = "";
      channels.series.forEach(function (item) {
        var span = document.createElement("span");
        span.className = "item";
        span.innerHTML = "<i class='dot' style='background:" + item.color + "'></i>" + escapeHtml(item.name);
        legend.appendChild(span);
      });
      if (state.fluxMode === "store") {
        var note = document.createElement("span");
        note.className = "item";
        note.textContent = "按「商品流量」最近一次采集快照展示";
        legend.appendChild(note);
      }
    }
  }

  function renderSettlement(data) {
    state.rows.settlement = data.records || [];
    state.storeNames = storeNames(data);
    resetPaging("settlement");
    paintSettlement();
    syncStoreFilter(data, (data.stores || []).length);
  }

  function paintSettlement() {
    var rows = state.rows.settlement;
    var names = state.storeNames || {};
    var body = $("#settlementRows");
    body.innerHTML = "";
    $("#settlementEmpty").hidden = rows.length > 0;
    $("#settlementHint").textContent = rows.length + " 条结算单";
    var info = pageSlice("settlement", rows);
    info.rows.forEach(function (row) {
      var p = row.payload || {};
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td class='store-name'>" + escapeHtml(names[row.store_code] || row.store_code) + "</td>" +
        "<td class='code'>" + escapeHtml(p.parent_order_sn || "-") + "</td>" +
        "<td>" + escapeHtml(p.status || "-") + "</td>" +
        "<td class='num'>¥" + money(p.sales_payback) + "</td>" +
        "<td class='num neg'>¥" + money(p.sales_reversal) + "</td>" +
        "<td class='num'>¥" + money(p.freight_payback) + "</td>" +
        "<td class='num neg'>¥" + money(p.freight_reversal) + "</td>" +
        "<td class='num'>¥" + money(p.declared_price) + "</td>" +
        "<td class='num'>" + integer(p.quantity) + "</td>" +
        "<td>" + escapeHtml(shortTime(p.captured_at)) + "</td>";
      body.appendChild(tr);
    });
    renderPager("#settlementPager", "settlement", info, paintSettlement);
  }

  function renderStoresPage(data) {
    var stores = data.all_stores || data.stores || [];
    var selfAccount = (data.scope && data.scope.account_id) || "";
    var body = $("#allStoreRows");
    body.innerHTML = "";
    $("#storesEmpty").hidden = stores.length > 0;
    $("#storesHint").textContent = stores.length + " 家店铺";
    stores.forEach(function (store) {
      var flags = Number(store.flag_count || 0);
      var code = String(store.store_code || "");
      var canDelete = String(store.owner_account_id || "") === selfAccount && Boolean(code);
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td class='store-name'>" + escapeHtml(store.store_name || "-") + "</td>" +
        "<td class='code'>" + escapeHtml(code || "-") + "</td>" +
        "<td>" + escapeHtml(store.staff_no || store.username || "-") + "</td>" +
        "<td>" + escapeHtml(store.platform || "temu") + "</td>" +
        "<td>" + escapeHtml(store.last_stat_date || "未上报") + "</td>" +
        "<td class='num'>" + (flags > 0
          ? "<span class='badge warn'>" + flags + "</span>"
          : "<span class='badge'>0</span>") + "</td>" +
        "<td class='num'>" + (canDelete
          ? "<button class='chip ghost danger' data-store-code='" + escapeHtml(code) +
            "' data-store-name='" + escapeHtml(store.store_name || code) + "'>删除</button>"
          : "<span class='muted'>—</span>") + "</td>";
      body.appendChild(tr);
    });
    body.querySelectorAll("[data-store-code]").forEach(function (button) {
      button.addEventListener("click", function () {
        void deleteStore(
          button.getAttribute("data-store-code"),
          button.getAttribute("data-store-name")
        );
      });
    });
    syncStoreFilter(data, stores.length);
  }

  async function deleteStore(code, name) {
    if (!code) return;
    var label = name || code;
    var confirmed = window.confirm(
      "删除店铺「" + label + "」？\n" +
      "该店铺的日汇总、订单/财务等明细、绑定关系和查重标记都会一起删除，店铺码不会回收。"
    );
    if (!confirmed) return;
    try {
      await api("/stores/" + encodeURIComponent(code), { method: "DELETE" });
      toast("已删除店铺：" + label);
      await load();
    } catch (error) {
      toast(error.message || String(error));
    }
  }

  async function load() {
    if (state.loading || !state.token) return;
    state.loading = true;
    var refresh = $("#refreshBtn");
    if (refresh) refresh.textContent = "刷新中…";
    try {
      if (state.page === "overview") {
        var results = await Promise.all([
          api("/overview?top=8&" + rangeParams()),
          api("/trends?top=6&" + rangeParams()),
        ]);
        render(results[0]);
        renderTrends(results[1]);
      } else if (state.page === "orders") {
        var extra = [];
        if (state.orderStatus) extra.push("status=" + encodeURIComponent(state.orderStatus));
        if (state.orderKeyword) extra.push("keyword=" + encodeURIComponent(state.orderKeyword));
        var orders = await api("/records?kind=order&limit=5000&" + rangeParams(extra));
        renderOrders(orders);
      } else if (state.page === "finance") {
        var finance = await api("/records?kind=finance&limit=5000&" + rangeParams());
        renderFinance(finance);
      } else if (state.page === "traffic") {
        var traffic = await api("/records?kind=traffic,sales&limit=5000&" + rangeParams());
        renderTraffic(traffic);
      } else if (state.page === "metrics") {
        var metricPair = await Promise.all([
          api("/records?kind=metric&limit=5000&" + rangeParams()),
          api("/batches?limit=50"),
        ]);
        renderMetrics(metricPair[0]);
        renderBatches(metricPair[1]);
      } else if (state.page === "settlement") {
        var settlements = await api("/records?kind=settlement&limit=5000&" + rangeParams());
        renderSettlement(settlements);
      } else if (state.page === "stores") {
        var stores = await api("/overview?top=1&" + rangeParams());
        renderStoresPage(stores);
      }
    } catch (error) {
      toast(error.message || String(error));
    } finally {
      state.loading = false;
      if (refresh) refresh.textContent = "刷新";
    }
  }

  function bind() {
    var form = $("#loginForm");
    if (form) form.addEventListener("submit", login);
    var logoutBtn = $("#logoutBtn");
    if (logoutBtn) logoutBtn.addEventListener("click", function () { logout(false); });
    var refresh = $("#refreshBtn");
    if (refresh) refresh.addEventListener("click", load);
    document.querySelectorAll(".nav-item").forEach(function (button) {
      button.addEventListener("click", function () {
        setPage(button.getAttribute("data-page") || "overview");
        load();
      });
    });
    var orderQuery = $("#orderQueryBtn");
    if (orderQuery) {
      orderQuery.addEventListener("click", function () {
        state.orderStatus = ($("#orderStatus") || {}).value || "";
        state.orderKeyword = (($("#orderKeyword") || {}).value || "").trim();
        load();
      });
    }
    var orderReset = $("#orderResetBtn");
    if (orderReset) {
      orderReset.addEventListener("click", function () {
        state.orderStatus = "";
        state.orderKeyword = "";
        if ($("#orderStatus")) $("#orderStatus").value = "";
        if ($("#orderKeyword")) $("#orderKeyword").value = "";
        load();
      });
    }
    var orderKeyword = $("#orderKeyword");
    if (orderKeyword) {
      orderKeyword.addEventListener("keydown", function (event) {
        if (event.key !== "Enter") return;
        state.orderStatus = ($("#orderStatus") || {}).value || "";
        state.orderKeyword = (event.target.value || "").trim();
        load();
      });
    }
    document.querySelectorAll("#fluxMode button").forEach(function (button) {
      button.addEventListener("click", function () {
        state.fluxMode = button.getAttribute("data-flux-mode") || "all";
        document.querySelectorAll("#fluxMode button").forEach(function (item) {
          item.classList.toggle("active", item === button);
        });
        renderFlux();
      });
    });
    var fluxStore = $("#fluxStore");
    if (fluxStore) {
      fluxStore.addEventListener("change", function () {
        state.fluxStore = fluxStore.value || "";
        renderFlux();
      });
    }
    document.querySelectorAll("#trendMode button").forEach(function (button) {
      button.addEventListener("click", function () {
        state.trendMode = button.getAttribute("data-mode") || "store";
        document.querySelectorAll("#trendMode button").forEach(function (item) {
          item.classList.toggle("active", item === button);
        });
        if (state.trends) renderCharts();
      });
    });
    document.querySelectorAll("#trendScale button").forEach(function (button) {
      button.addEventListener("click", function () {
        chartState.scale = button.getAttribute("data-scale") || "day";
        document.querySelectorAll("#trendScale button").forEach(function (item) {
          item.classList.toggle("active", item === button);
        });
        resetChartWindow();
        renderCharts();
      });
    });
    ["#trendOrders", "#trendAmount"].forEach(function (selector) {
      var el = $(selector);
      if (!el) return;
      // 滚轮缩放：以鼠标位置为中心，可以放大到具体的某一天
      el.addEventListener("wheel", function (event) {
        if (!state.trends) return;
        event.preventDefault();
        zoomChart(event.deltaY < 0 ? 1 : -1, event.clientX, el);
      }, { passive: false });
      // 拖动平移：看更早/更晚的区间
      el.addEventListener("mousedown", function (event) {
        chartState.dragging = { x: event.clientX, el: el };
        el.classList.add("dragging");
        event.preventDefault();
      });
      // 双击还原到完整区间
      el.addEventListener("dblclick", function () {
        resetChartWindow();
        renderCharts();
      });
    });
    window.addEventListener("mousemove", function (event) {
      var dragging = chartState.dragging;
      if (!dragging) return;
      var delta = event.clientX - dragging.x;
      if (Math.abs(delta) < 4) return;
      dragging.x = event.clientX;
      panChart(delta, dragging.el);
    });
    window.addEventListener("mouseup", function () {
      if (!chartState.dragging) return;
      chartState.dragging.el.classList.remove("dragging");
      chartState.dragging = null;
    });
    var filterBtn = $("#storeFilterBtn");
    var filterPop = $("#storeFilterPop");
    if (filterBtn && filterPop) {
      filterBtn.addEventListener("click", function (event) {
        event.stopPropagation();
        filterPop.hidden = !filterPop.hidden;
      });
      filterPop.addEventListener("click", function (event) {
        event.stopPropagation();
      });
      document.addEventListener("click", function () {
        filterPop.hidden = true;
      });
      document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") filterPop.hidden = true;
      });
    }
    document.querySelectorAll("[data-range]").forEach(function (button) {
      button.addEventListener("click", function () {
        state.range = button.getAttribute("data-range");
        document.querySelectorAll("[data-range]").forEach(function (item) {
          item.classList.toggle("active", item === button);
        });
        load();
      });
    });
    window.addEventListener("resize", function () {
      window.clearTimeout(bind._resize);
      bind._resize = window.setTimeout(function () {
        if (state.trends && state.page === "overview") {
          renderCharts();
          return;
        }
        if (state.token) load();
      }, 220);
    });
  }

  bind();
  if (state.token) {
    setView("dashboard");
    setPage("overview");
    load();
  } else {
    setView("auth");
  }
})();
