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
    page: "boss",
    bossRank: "store",
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
      setPage("boss");
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
      // pathLength=1 让 stroke-dasharray:1 能覆盖整条曲线，CSS 里据此做「描线」入场动画。
      stroke.setAttribute("pathLength", "1");
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
    var pricePending = 0;
    var notPublished = 0;
    var published = 0;
    var delisted = 0;
    codes.forEach(function (code) {
      var payload = latest[code].payload;
      var rowPending = Number(payload.pending_shipment || 0);
      var rowActive = Number(payload.active_products || 0);
      var rowSales = Number(payload.sales_30d || 0);
      pending += rowPending;
      active += rowActive;
      sales30 += rowSales;
      pricePending += Number(payload.listing_price_pending || 0);
      notPublished += Number(payload.listing_not_published || 0);
      published += Number(payload.listing_published || 0);
      delisted += Number(payload.listing_delisted || 0);
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td class='store-name'>" + escapeHtml(names[code] || code) + "</td>" +
        "<td class='code'>" + escapeHtml(code) + "</td>" +
        "<td>" + escapeHtml(staff[code] || "-") + "</td>" +
        "<td class='num'>" + integer(rowPending) + "</td>" +
        "<td class='num'>" + integer(rowActive) + "</td>" +
        "<td class='num'>" + integer(payload.listing_price_pending) + "</td>" +
        "<td class='num'>" + integer(payload.listing_not_published) + "</td>" +
        "<td class='num pos'>" + integer(payload.listing_published) + "</td>" +
        "<td class='num'>" + integer(payload.listing_delisted) + "</td>" +
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
      sumCard("价格申报中合计", integer(pricePending)) +
      sumCard("未发布合计", integer(notPublished)) +
      sumCard("已发布合计", integer(published)) +
      sumCard("已下架合计", integer(delisted)) +
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

  // ---------- 经营驾驶舱 ----------
  // 只看三件事：赚了多少、钱花在哪、有什么风险。
  // 所以这一页不做大表格，全部用「结论卡 + 白话解释 + 金额化风险」。
  var BOSS_COLOR = {
    brand: "#10b981",
    blue: "#3b82f6",
    amber: "#f59e0b",
    slate: "#94a3b8",
    danger: "#ef4444"
  };

  function bossNumber(value) {
    var number = Number(value);
    return Number.isFinite(number) ? number : 0;
  }

  function bossMoney(value) {
    var number = Number(value) || 0;
    return (number < 0 ? "-" : "") + "¥" + money(Math.abs(number));
  }

  function bossCompact(value) {
    var number = Number(value) || 0;
    var sign = number < 0 ? "-" : "";
    var abs = Math.abs(number);
    if (abs >= 100000000) return sign + (abs / 100000000).toFixed(2) + "亿";
    if (abs >= 10000) return sign + (abs / 10000).toFixed(1) + "万";
    return sign + integer(abs);
  }

  function bossDelta(current, previous) {
    var cur = Number(current);
    var prev = Number(previous);
    if (!Number.isFinite(cur) || !Number.isFinite(prev) || prev === 0) return null;
    return (cur - prev) / Math.abs(prev);
  }

  function bossDeltaChip(delta) {
    if (delta == null) return "<span class='boss-delta is-flat'>上期无对比</span>";
    return "<span class='boss-delta " + (delta >= 0 ? "is-up" : "is-down") + "'>" +
      (delta >= 0 ? "▲ " : "▼ ") + Math.abs(delta * 100).toFixed(1) + "%</span>" +
      "<span class='boss-delta-label'>较上期</span>";
  }

  function bossSparkline(values, color) {
    var list = (values || []).map(function (value) { return Number(value) || 0; });
    if (list.length < 2) return "";
    var width = 140;
    var height = 36;
    var pad = 3;
    var min = Math.min.apply(null, list);
    var max = Math.max.apply(null, list);
    var span = max - min || 1;
    var points = list.map(function (value, index) {
      return [
        pad + ((width - pad * 2) * index) / (list.length - 1),
        height - pad - ((height - pad * 2) * (value - min)) / span
      ];
    });
    var line = smoothPath(points);
    var area = line +
      " L" + points[points.length - 1][0].toFixed(1) + " " + (height - pad) +
      " L" + points[0][0].toFixed(1) + " " + (height - pad) + " Z";
    return "<svg viewBox='0 0 " + width + " " + height + "' preserveAspectRatio='none' aria-hidden='true'>" +
      "<path d='" + area + "' fill='" + color + "' opacity='.12'></path>" +
      "<path d='" + line + "' fill='none' stroke='" + color + "' stroke-width='2' stroke-linecap='round'></path>" +
      "</svg>";
  }

  function bossEmptyArt(title, desc) {
    return "<div class='boss-empty'>" +
      "<svg viewBox='0 0 96 72' aria-hidden='true'>" +
      "<rect x='10' y='28' width='52' height='32' rx='6' fill='none' stroke='currentColor' stroke-width='2'/>" +
      "<path d='M10 36h52' stroke='currentColor' stroke-width='2'/>" +
      "<path d='M30 28l6-12h18l6 12' fill='none' stroke='currentColor' stroke-width='2' stroke-linejoin='round'/>" +
      "<circle cx='74' cy='52' r='10' fill='none' stroke='currentColor' stroke-width='2'/>" +
      "<path d='M70 52l3 3 6-6' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/>" +
      "</svg>" +
      "<strong>" + title + "</strong><p>" + desc + "</p></div>";
  }

  function bossHealthChip(label, value, tone, tip) {
    return "<span class='boss-chip " + (tone || "") + "'" +
      (tip ? " title='" + escapeHtml(tip) + "'" : "") + ">" +
      "<b>" + escapeHtml(value) + "</b>" + escapeHtml(label) + "</span>";
  }

  function bossHeroCard(config) {
    var raw = config.raw == null ? "" : String(config.raw);
    return "<article class='boss-card " + (config.tone || "") + "'>" +
      "<div class='boss-card-top'><span class='boss-card-label'>" + config.label + "</span>" +
      (config.tip ? "<i class='boss-tip' title='" + escapeHtml(config.tip) + "'>?</i>" : "") +
      "</div>" +
      "<strong class='boss-card-value' data-value='0' data-target='" + escapeHtml(raw) +
        "' data-format='" + config.format + "'>" + config.display + "</strong>" +
      "<div class='boss-card-foot'>" + (config.foot || "") + "</div>" +
      "<p class='boss-card-hint'>" + config.hint + "</p>" +
      "<div class='boss-spark'>" + (config.spark || "") + "</div>" +
      "</article>";
  }

  /** 只统计「利润算得出来」的订单：这类订单的 GMV 与各项成本能对得上。 */
  function bossOrderCosts(rows) {
    var summary = { count: 0, incomplete: 0, gmv: 0, commission: 0, purchase: 0, head: 0, tail: 0, profit: 0 };
    (rows || []).forEach(function (row) {
      if (row.kind !== "order") return;
      var payload = row.payload || {};
      if (payload.profit_estimate_cny == null) {
        summary.incomplete += 1;
        return;
      }
      summary.count += 1;
      summary.gmv += bossNumber(payload.gmv_cny);
      summary.commission += bossNumber(payload.commission_cny);
      summary.purchase += bossNumber(payload.purchase_cost_cny);
      summary.head += bossNumber(payload.head_freight_cny);
      summary.tail += bossNumber(payload.tail_freight_cny);
      summary.profit += bossNumber(payload.profit_estimate_cny);
    });
    return summary;
  }

  function bossFinanceSummary(rows) {
    var summary = { total: 0, ad: 0, penalty: 0, other: 0 };
    (rows || []).forEach(function (row) {
      if (row.kind !== "finance") return;
      var amount = Number((row.payload || {}).amount || 0);
      if (amount >= 0) return;
      var value = -amount;
      var category = String((row.payload || {}).category || "");
      summary.total += value;
      if (category === "promotion") summary.ad += value;
      else if (category === "penalty") summary.penalty += value;
      else summary.other += value;
    });
    return summary;
  }

  function bossLatestMetrics(rows) {
    var map = {};
    (rows || []).forEach(function (row) {
      if (row.kind !== "metric") return;
      var code = String(row.store_code || "");
      if (!code) return;
      var payload = row.payload || {};
      var stamp = String(payload.captured_at || row.stat_date || "");
      if (!map[code] || stamp > map[code].stamp) map[code] = { stamp: stamp, payload: payload };
    });
    return map;
  }

  function renderBoss(overview, records, financeRecords, orderRecords, previous) {
    var totals = (overview && overview.totals) || {};
    var stores = (overview && overview.all_stores) || [];
    var storeRows = (overview && overview.top_stores) || [];
    var trend = (overview && overview.trend) || [];
    var byAccount = (overview && overview.by_account) || [];
    var prevTotals = (previous && previous.totals) || null;

    var costs = bossOrderCosts(orderRecords);
    var expenses = bossFinanceSummary(financeRecords);
    var financeTotal = bossNumber(totals.finance_expense_cny) || expenses.total;
    var adCost = expenses.ad;
    var penalty = expenses.penalty;
    var otherCost = Math.max(0, financeTotal - adCost - penalty);

    var sales = bossNumber(totals.gmv_cny);
    var hasCostChain = costs.count > 0;
    var chainSales = hasCostChain ? costs.gmv : sales;
    var orderProfit = hasCostChain
      ? costs.profit
      : (totals.profit_cny == null ? null : bossNumber(totals.profit_cny));
    var netProfit = orderProfit == null ? null : orderProfit - financeTotal;
    var payback = bossNumber(totals.settlement_payback_cny);
    var pendingCash = Math.max(0, sales - payback);
    var paybackRate = sales > 0 ? payback / sales : null;

    var prevNet = null;
    if (prevTotals && prevTotals.profit_cny != null) {
      prevNet = bossNumber(prevTotals.profit_cny) - bossNumber(prevTotals.finance_expense_cny);
    }

    var trendDates = trend.map(function (row) { return String(row.key || row.label || ""); });
    var gmvSeries = trend.map(function (row) { return bossNumber(row.gmv_cny); });
    var netSeries = trend.map(function (row) {
      if (row.profit_cny == null) return 0;
      return bossNumber(row.profit_cny) - bossNumber(row.finance_expense_cny);
    });

    // 数据健康条：最先要确认的是「这些数字能不能信」。
    var lastDate = String((overview && overview.end) || "").slice(0, 10);
    if (!lastDate && trendDates.length) lastDate = trendDates[trendDates.length - 1];
    var withData = stores.filter(function (item) {
      return String(item.last_stat_date || "") !== "";
    }).length;
    var profitStores = storeRows.filter(function (item) { return item.profit_cny != null; }).length;
    var cutoffDate = lastDate ? iso(addDays(new Date(lastDate + "T00:00:00"), -7)) : "";
    var staleStores = stores.filter(function (item) {
      var stamp = String(item.last_stat_date || "");
      return !stamp || (cutoffDate !== "" && stamp < cutoffDate);
    }).length;
    $("#bossHealth").innerHTML =
      bossHealthChip("数据截止", lastDate ? lastDate.slice(5) : "暂无", "", "最近一天有经营数据的日期") +
      bossHealthChip("覆盖店铺", withData + "/" + stores.length, withData === stores.length ? "is-ok" : "is-warn", "有上报数据的店铺数 / 团队店铺总数") +
      bossHealthChip("利润完整", profitStores + "/" + storeRows.length, profitStores === storeRows.length ? "is-ok" : "is-warn", "能算出利润的店铺：需要维护采购价和重量") +
      (staleStores
        ? bossHealthChip("超 7 天未报", staleStores + " 家", "is-danger", "这些店铺的数据可能已经过期")
        : bossHealthChip("上报状态", "全部新鲜", "is-ok", "所有店铺 7 天内有数据更新"));

    if (!storeRows.length && !trend.length) {
      $("#bossHero").innerHTML = bossEmptyArt(
        "还没有可以看的经营数据",
        "员工在工作台采集数据并点「提交本周汇总」后，这里会自动出现结论。"
      );
      $("#bossWaterfall").innerHTML = "";
      $("#bossWaterfallNote").textContent = "";
      $("#bossRisks").innerHTML = bossEmptyArt("暂时没有风险项", "有数据后会自动列出待处理事项。");
      $("#bossFunnel").innerHTML = "";
      $("#bossRank").innerHTML = "";
      $("#bossTrendChart").innerHTML = "";
      $("#bossTrendLegend").innerHTML = "";
      $("#bossProfitBadge").textContent = "—";
      return;
    }

    $("#bossHero").innerHTML =
      bossHeroCard({
        label: "净利",
        raw: netProfit,
        format: "money",
        display: netProfit == null ? "—" : bossMoney(netProfit),
        tone: netProfit == null ? "" : (netProfit >= 0 ? "is-good" : "is-bad"),
        tip: "订单利润减去广告、罚款等费用，才是真正落袋的钱。",
        hint: "扣掉货款、运费、佣金、广告和罚款后，真正赚到的钱。",
        foot: bossDeltaChip(bossDelta(netProfit, prevNet)) +
          "<span class='boss-delta-label'>利润完整 " + profitStores + "/" + storeRows.length + " 店</span>",
        spark: bossSparkline(netSeries, netProfit != null && netProfit < 0 ? BOSS_COLOR.danger : BOSS_COLOR.brand)
      }) +
      bossHeroCard({
        label: "销售额",
        raw: sales,
        format: "money",
        display: bossMoney(sales),
        tip: "买家一共付了多少钱，还没有扣任何成本。",
        hint: "买家一共付了多少钱，还没扣成本。",
        foot: bossDeltaChip(bossDelta(sales, prevTotals ? prevTotals.gmv_cny : null)),
        spark: bossSparkline(gmvSeries, BOSS_COLOR.blue)
      }) +
      bossHeroCard({
        label: "已回款",
        raw: payback,
        format: "money",
        display: bossMoney(payback),
        tip: "平台已经结算到账的钱，和销售额不是一回事。",
        hint: "平台已经结算到账的钱，通常比销售额晚一段时间。",
        foot: "<span class='boss-delta-label'>回款率 " + (paybackRate == null ? "—" : (paybackRate * 100).toFixed(0) + "%") + "</span>",
        spark: bossSparkline(trend.map(function (row) { return bossNumber(row.settlement_payback_cny); }), BOSS_COLOR.amber)
      }) +
      bossHeroCard({
        label: "待回款",
        raw: pendingCash,
        format: "money",
        display: bossMoney(pendingCash),
        tone: pendingCash > 0 ? "is-pending" : "",
        tip: "卖出去但平台还没结算的钱，通常压在账期里。",
        hint: "卖出去了、还没结算的钱，压在平台账期里。",
        foot: "<span class='boss-delta-label'>销售额 − 已回款</span>",
        spark: bossSparkline(trend.map(function (row) {
          return Math.max(0, bossNumber(row.gmv_cny) - bossNumber(row.settlement_payback_cny));
        }), BOSS_COLOR.slate)
      });

    document.querySelectorAll("#bossHero .boss-card-value").forEach(function (el) {
      var target = Number(el.getAttribute("data-target"));
      if (!Number.isFinite(target)) return;
      var format = el.getAttribute("data-format");
      countUp(el, target, format === "money"
        ? function (value) { return bossMoney(value); }
        : function (value) { return integer(value); });
    });

    paintBossWaterfall({
      chainSales: chainSales,
      hasCostChain: hasCostChain,
      costs: costs,
      orderProfit: orderProfit,
      adCost: adCost,
      penalty: penalty,
      otherCost: otherCost,
      netProfit: netProfit,
      orderRecords: orderRecords
    });

    renderBossRisks({
      metricRows: records,
      penalty: penalty,
      staleStores: staleStores
    });

    renderBossFunnel(totals);

    state.boss = { storeRows: storeRows, byAccount: byAccount };
    paintBossRank();

    $("#bossTrendHint").textContent = (trendDates.length ? trendDates[0] + " ~ " + trendDates[trendDates.length - 1] : "—") +
      " · 净利只统计已核算成本的订单";
    drawBossTrend($("#bossTrendChart"), trendDates, [
      { name: "销售额", color: BOSS_COLOR.blue, values: gmvSeries },
      { name: "净利", color: BOSS_COLOR.brand, values: netSeries }
    ]);
    $("#bossTrendLegend").innerHTML = [
      { name: "销售额", color: BOSS_COLOR.blue },
      { name: "净利（已扣费用）", color: BOSS_COLOR.brand }
    ].map(function (item) {
      return "<span class='item'><i class='dot' style='background:" + item.color + "'></i>" +
        escapeHtml(item.name) + "</span>";
    }).join("");
  }

  function paintBossWaterfall(state) {
    var el = $("#bossWaterfall");
    if (!el) return;
    var base = state.chainSales > 0 ? state.chainSales : 0;
    var running = base;
    var rows = [];
    rows.push({ label: "销售额", value: base, kind: "is-start" });

    if (state.hasCostChain) {
      [
        { label: "平台佣金", value: state.costs.commission, hint: "平台按成交金额收取的佣金" },
        { label: "采购成本", value: state.costs.purchase, hint: "商品进货花的钱" },
        { label: "头程运费", value: state.costs.head, hint: "从国内发到平台仓库的运费" },
        { label: "尾程运费", value: state.costs.tail, hint: "平台配送给买家的运费" }
      ].forEach(function (step) {
        if (step.value <= 0) return;
        running -= step.value;
        rows.push({
          label: step.label,
          delta: step.value,
          value: Math.max(0, running),
          kind: "is-minus",
          hint: step.hint
        });
      });
    }

    var profitValue = state.hasCostChain ? Math.max(0, running) : state.orderProfit;
    if (profitValue != null) {
      running = profitValue;
      rows.push({
        label: state.hasCostChain ? "订单利润" : "订单利润（汇总）",
        value: Math.max(0, running),
        kind: "is-subtotal",
        hint: state.hasCostChain ? "销售额扣掉佣金、采购和运费后剩下的钱" : "平台订单利润合计，成本明细暂未拆分"
      });
    }

    if (running != null) {
      [
        { label: "广告推广", value: state.adCost, hint: "投广告花的钱" },
        { label: "平台罚款", value: state.penalty, hint: "违规或超时被平台扣的钱" },
        { label: "其他费用", value: state.otherCost, hint: "对账中心里的其他支出" }
      ].forEach(function (step) {
        if (step.value <= 0) return;
        running -= step.value;
        rows.push({
          label: step.label,
          delta: step.value,
          value: Math.max(0, running),
          kind: "is-minus",
          hint: step.hint
        });
      });
    }

    rows.push({
      label: "净利",
      value: running == null ? 0 : Math.max(0, running),
      kind: "is-final" + (running != null && running < 0 ? " is-negative" : ""),
      hint: "真正落袋的钱",
      empty: running == null
    });

    el.innerHTML = rows.map(function (row) {
      var width = base > 0 ? Math.max(2, Math.min(100, (row.value / base) * 100)) : 2;
      var valueText = row.empty
        ? "—"
        : (row.kind === "is-minus" ? "−" + bossMoney(row.delta) : bossMoney(row.value));
      return "<div class='wf-row " + row.kind + "'" + (row.hint ? " title='" + escapeHtml(row.hint) + "'" : "") + ">" +
        "<div class='wf-head'><span class='wf-label'>" + row.label + "</span>" +
        "<span class='wf-value'>" + valueText + "</span></div>" +
        "<div class='wf-track'><i style='width:" + width.toFixed(1) + "%'></i></div>" +
        "</div>";
    }).join("");

    var margin = state.netProfit != null && state.chainSales > 0 ? state.netProfit / state.chainSales : null;
    $("#bossProfitBadge").textContent = margin == null
      ? "利润数据不足"
      : "净利率 " + (margin * 100).toFixed(1) + "%";

    var notes = [];
    if (state.hasCostChain) {
      notes.push("按已核算成本的 " + integer(state.costs.count) + " 条订单计算");
    } else if (state.orderProfit != null) {
      notes.push("本期没有订单成本明细，订单利润改用平台利润汇总估算");
    }
    if (state.costs.incomplete) {
      notes.push("另有 " + integer(state.costs.incomplete) + " 条订单缺采购价或重量，未计入净利");
    }
    if ((state.orderRecords || []).length >= 4900) {
      notes.push("订单明细超过 5000 条，成本按最近 5000 条估算");
    }
    if (!notes.length) notes.push("本期还没有能核算成本的订单，净利暂时算不出来");
    $("#bossWaterfallNote").textContent = notes.join("；") + "。";
  }

  function renderBossRisks(data) {
    var el = $("#bossRisks");
    if (!el) return;
    var map = bossLatestMetrics(data.metricRows);
    var sums = { pending: 0, refund: 0, cancel: 0, pricePending: 0, notPublished: 0, delisted: 0 };
    Object.keys(map).forEach(function (code) {
      var payload = map[code].payload || {};
      sums.pending += bossNumber(payload.pending_shipment);
      sums.refund += bossNumber(payload.refund_pending);
      sums.cancel += bossNumber(payload.cancel_pending);
      sums.pricePending += bossNumber(payload.listing_price_pending);
      sums.notPublished += bossNumber(payload.listing_not_published);
      sums.delisted += bossNumber(payload.listing_delisted);
    });

    var items = [];
    if (data.penalty > 0) {
      items.push({
        title: "平台罚款 " + bossMoney(data.penalty),
        hint: "财务流水里能看到罚款原因，金额直接从利润里扣",
        value: "罚款",
        tone: "is-danger"
      });
    }
    if (sums.pending > 0) {
      items.push({
        title: integer(sums.pending) + " 单还没发货",
        hint: "超时发货会被平台扣分，尽快安排",
        value: integer(sums.pending) + " 单",
        tone: "is-warn"
      });
    }
    if (sums.refund > 0) {
      items.push({
        title: integer(sums.refund) + " 单退货退款待处理",
        hint: "买家在等处理，拖久了容易变纠纷",
        value: integer(sums.refund) + " 单",
        tone: "is-warn"
      });
    }
    if (sums.cancel > 0) {
      items.push({
        title: integer(sums.cancel) + " 单取消待处理",
        hint: "买家申请取消，需要及时响应",
        value: integer(sums.cancel) + " 单",
        tone: "is-warn"
      });
    }
    if (sums.delisted > 0) {
      items.push({
        title: integer(sums.delisted) + " 个商品已下架",
        hint: "确认是否误下架，能卖的重新上架",
        value: integer(sums.delisted) + " 个",
        tone: "is-warn"
      });
    }
    if (sums.pricePending > 0) {
      items.push({
        title: integer(sums.pricePending) + " 个新品卡在价格审核",
        hint: "审核通过才能开卖，卡住就没有新增供给",
        value: integer(sums.pricePending) + " 个",
        tone: "is-muted"
      });
    }
    if (sums.notPublished > 0) {
      items.push({
        title: integer(sums.notPublished) + " 个商品还没上架",
        hint: "已经建好但没发布，等于没开始卖",
        value: integer(sums.notPublished) + " 个",
        tone: "is-muted"
      });
    }
    if (data.staleStores > 0) {
      items.push({
        title: data.staleStores + " 家店超过 7 天没上报",
        hint: "这些店的数字可能是旧的，决策前先确认",
        value: data.staleStores + " 家",
        tone: "is-danger"
      });
    }

    if (!items.length) {
      el.innerHTML = "<li class='boss-risk-empty'>" + bossEmptyArt(
        "这段时间没有需要处理的异常",
        "待发货、退款、罚款和上新阻塞都会自动出现在这里。"
      ) + "</li>";
      return;
    }
    var shown = items.slice(0, 6);
    el.innerHTML = shown.map(function (item) {
      return "<li class='boss-risk " + item.tone + "'>" +
        "<span class='boss-risk-dot'></span>" +
        "<div class='boss-risk-body'><strong>" + item.title + "</strong><p>" + item.hint + "</p></div>" +
        "<span class='boss-risk-value'>" + item.value + "</span>" +
        "</li>";
    }).join("") + (items.length > shown.length
      ? "<li class='boss-risk-more'>还有 " + (items.length - shown.length) + " 项，去「店铺指标」看全部</li>"
      : "");
  }

  function renderBossFunnel(totals) {
    var el = $("#bossFunnel");
    if (!el) return;
    var exposure = bossNumber(totals.exposure);
    var clicks = bossNumber(totals.clicks);
    var orders = bossNumber(totals.order_count);
    if (exposure <= 0) {
      el.innerHTML = bossEmptyArt(
        "还没有流量数据",
        "员工在经营分析里采集一次「商品流量」，这里就会显示曝光到成交。"
      );
      return;
    }
    var maxValue = Math.max(exposure, clicks, orders, 1);
    function width(value) {
      return Math.max(6, Math.round((100 * Math.log(1 + value)) / Math.log(1 + maxValue)));
    }
    var clickRate = exposure > 0 ? (clicks / exposure) * 100 : null;
    var orderRate = clicks > 0 ? (orders / clicks) * 100 : null;
    el.innerHTML = [
      { label: "曝光", value: exposure, suffix: "次", note: "商品被看到的总次数" },
      { label: "点击", value: clicks, suffix: "次", note: clickRate == null ? "" : "点击率 " + clickRate.toFixed(1) + "%" },
      { label: "成交", value: orders, suffix: "单", note: orderRate == null ? "" : "点击后下单 " + orderRate.toFixed(1) + "%" }
    ].map(function (row, index) {
      return "<div class='funnel-row'>" +
        "<div class='funnel-head'><span class='funnel-label'>" + row.label + "</span>" +
        "<span class='funnel-value'>" + integer(row.value) + " " + row.suffix + "</span></div>" +
        "<div class='funnel-track'><i style='width:" + (index === 0 ? 100 : width(row.value)) + "%'></i></div>" +
        (row.note ? "<p class='funnel-note'>" + row.note + "</p>" : "") +
        "</div>";
    }).join("");
  }

  function paintBossRank() {
    var el = $("#bossRank");
    if (!el || !state.boss) return;
    var mode = state.bossRank || "store";
    var source = mode === "staff" ? state.boss.byAccount : state.boss.storeRows;
    var rows = (source || []).map(function (row) {
      var profit = row.profit_cny == null ? null : bossNumber(row.profit_cny);
      var expense = bossNumber(row.finance_expense_cny);
      return {
        name: String(row.label || row.username || row.key || "—"),
        net: profit == null ? null : profit - expense,
        gmv: bossNumber(row.gmv_cny),
        orders: bossNumber(row.order_count)
      };
    });
    var hasNet = rows.some(function (row) { return row.net != null; });
    rows.sort(function (left, right) {
      var a = hasNet ? (left.net == null ? -Infinity : left.net) : left.gmv;
      var b = hasNet ? (right.net == null ? -Infinity : right.net) : right.gmv;
      return b - a;
    });
    rows = rows.slice(0, 8);
    if (!rows.length) {
      el.innerHTML = bossEmptyArt("还没有店铺或员工数据", "提交汇总后，这里会按赚钱多少排序。");
      return;
    }
    var maxValue = Math.max.apply(null, rows.map(function (row) {
      return Math.abs(hasNet ? (row.net || 0) : row.gmv);
    }).concat([1]));
    $("#bossRankHint").textContent = hasNet
      ? "净利 = 订单利润 − 费用（按" + (mode === "staff" ? "员工" : "店铺") + "）"
      : "利润数据不足，先按销售额排序";
    el.innerHTML = rows.map(function (row, index) {
      var value = hasNet ? (row.net || 0) : row.gmv;
      var width = Math.max(4, Math.round((Math.abs(value) / maxValue) * 100));
      var tone = hasNet && value < 0 ? " is-negative" : "";
      var valueText = hasNet
        ? (row.net == null ? "利润待补" : bossMoney(row.net))
        : bossMoney(row.gmv);
      return "<div class='boss-rank-row" + tone + "'>" +
        "<span class='boss-rank-no'>" + (index + 1) + "</span>" +
        "<div class='boss-rank-body'>" +
          "<div class='boss-rank-top'><strong>" + escapeHtml(row.name) + "</strong>" +
          "<span class='boss-rank-net'>" + valueText + "</span></div>" +
          "<div class='boss-rank-track'><i style='width:" + width + "%'></i></div>" +
          "<small>销售额 " + bossMoney(row.gmv) + " · 订单 " + integer(row.orders) + "</small>" +
        "</div>" +
      "</div>";
    }).join("");
  }

  /** 驾驶舱趋势：同时画销售额和净利，负值也能落在坐标里。 */
  function drawBossTrend(el, dates, series) {
    if (!el) return;
    el.innerHTML = "";
    if (!dates.length || !series.length) {
      el.innerHTML = "<div class='empty'><strong>这段时间没有走势</strong><p>员工提交周汇总后，这里会显示每天的销售额和净利。</p></div>";
      return;
    }
    var width = el.clientWidth || 720;
    var height = el.clientHeight || 260;
    var pad = { left: 58, right: 16, top: 16, bottom: 26 };
    var all = [];
    series.forEach(function (item) {
      (item.values || []).forEach(function (value) { all.push(Number(value) || 0); });
    });
    var max = Math.max.apply(null, all.concat([0]));
    var min = Math.min.apply(null, all.concat([0]));
    if (max === min) max = min + 1;

    function xAt(index) {
      if (dates.length === 1) return (pad.left + width - pad.right) / 2;
      return pad.left + ((width - pad.left - pad.right) * index) / (dates.length - 1);
    }
    function yAt(value) {
      return pad.top + (height - pad.top - pad.bottom) * (1 - ((Number(value) || 0) - min) / (max - min));
    }

    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("preserveAspectRatio", "none");

    var grid = document.createElementNS("http://www.w3.org/2000/svg", "g");
    grid.setAttribute("class", "grid");
    for (var line = 0; line <= 3; line += 1) {
      var value = max - ((max - min) * line) / 3;
      var y = yAt(value);
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
      label.textContent = "¥" + bossCompact(value);
      grid.appendChild(label);
    }
    svg.appendChild(grid);

    if (min < 0) {
      var zero = document.createElementNS("http://www.w3.org/2000/svg", "line");
      zero.setAttribute("x1", pad.left);
      zero.setAttribute("x2", width - pad.right);
      zero.setAttribute("y1", yAt(0));
      zero.setAttribute("y2", yAt(0));
      zero.setAttribute("stroke-dasharray", "4 4");
      zero.setAttribute("stroke", "currentColor");
      zero.setAttribute("opacity", ".35");
      svg.appendChild(zero);
    }

    series.forEach(function (item) {
      var points = (item.values || []).map(function (value, index) {
        return [xAt(index), yAt(value)];
      });
      var path = smoothPath(points);
      var stroke = document.createElementNS("http://www.w3.org/2000/svg", "path");
      stroke.setAttribute("class", "series-line");
      stroke.setAttribute("pathLength", "1");
      stroke.setAttribute("d", path);
      stroke.setAttribute("fill", "none");
      stroke.setAttribute("stroke", item.color);
      stroke.setAttribute("stroke-width", "2.4");
      stroke.setAttribute("stroke-linecap", "round");
      stroke.setAttribute("stroke-linejoin", "round");
      svg.appendChild(stroke);
      if (dates.length <= 40) {
        points.forEach(function (point, index) {
          var dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
          dot.setAttribute("class", "series-dot");
          dot.setAttribute("cx", point[0]);
          dot.setAttribute("cy", point[1]);
          dot.setAttribute("r", 3);
          dot.setAttribute("fill", "var(--surface)");
          dot.setAttribute("stroke", item.color);
          var tip = document.createElementNS("http://www.w3.org/2000/svg", "title");
          tip.textContent = dates[index] + " · " + item.name + " " + bossMoney(item.values[index]);
          dot.appendChild(tip);
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

  function previousRangeParams() {
    if (state.range === "all") return "";
    var dates = rangeDates(state.range);
    var start = new Date(dates.start + "T00:00:00");
    var end = new Date(dates.end + "T00:00:00");
    var days = Math.round((end.getTime() - start.getTime()) / 86400000) + 1;
    var prevEnd = addDays(start, -1);
    var prevStart = addDays(prevEnd, -(days - 1));
    var parts = ["from=" + iso(prevStart), "to=" + iso(prevEnd)];
    if (state.hiddenStores.length) {
      parts.push("exclude=" + encodeURIComponent(state.hiddenStores.join(",")));
    }
    return parts.join("&");
  }

  async function load() {
    if (state.loading || !state.token) return;
    state.loading = true;
    var refresh = $("#refreshBtn");
    if (refresh) refresh.textContent = "刷新中…";
    try {
      if (state.page === "boss") {
        var bossRange = rangeParams();
        var bossPrev = previousRangeParams();
        var bossCalls = [
          api("/overview?top=50&" + bossRange),
          api("/records?kind=metric,traffic,sales&limit=5000&" + bossRange),
          api("/records?kind=finance&limit=5000&" + bossRange),
          api("/records?kind=order&limit=5000&" + bossRange)
        ];
        if (bossPrev) bossCalls.push(api("/overview?top=50&" + bossPrev));
        var bossData = await Promise.all(bossCalls);
        renderBoss(
          bossData[0],
          (bossData[1] && bossData[1].records) || [],
          (bossData[2] && bossData[2].records) || [],
          (bossData[3] && bossData[3].records) || [],
          bossPrev ? bossData[4] : null
        );
      } else if (state.page === "overview") {
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
    document.querySelectorAll("#bossRankMode button").forEach(function (button) {
      button.addEventListener("click", function () {
        state.bossRank = button.getAttribute("data-rank") || "store";
        document.querySelectorAll("#bossRankMode button").forEach(function (item) {
          item.classList.toggle("active", item === button);
        });
        paintBossRank();
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
    setPage("boss");
    load();
  } else {
    setView("auth");
  }
})();
