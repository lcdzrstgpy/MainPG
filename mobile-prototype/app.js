/* ================================================================
   MainPG 安卓伴侣 App · 原型交互
   导航栈 + Tab 切换 + 排队动画 + 轻量状态模拟
   无构建依赖，直接以 file:// 或静态服务打开均可。
   ================================================================ */
(function () {
  'use strict';

  var DARK_STATUS = ['s-home', 's-camera', 's-compose'];   // 状态栏用白色文字的页面

  var dock = document.getElementById('dock');
  var fab = document.getElementById('fab');
  var statusbar = document.getElementById('statusbar');
  var toastEl = document.getElementById('toast');

  /* 原型内可变状态 */
  var state = {
    points: 1280,
    signed: false,
    signStreak: 4,
    unread: 2,
    plan: { name: '100 元 / 10,000 积分', price: '¥100' }
  };

  /* 导航栈：起始落在登录页 */
  var stack = [{ id: 's-login', isTab: false }];

  /* ---------------- 渲染 ---------------- */
  function cur() { return stack[stack.length - 1]; }

  function render() {
    var c = cur();

    Array.prototype.forEach.call(document.querySelectorAll('.screen'), function (el) {
      el.classList.toggle('active', el.id === c.id);
    });

    var active = document.getElementById(c.id);
    if (active) active.scrollTop = 0;

    dock.classList.toggle('hidden', !c.isTab);
    dock.classList.toggle('dark-notch', c.id === 's-camera');
    statusbar.classList.toggle('light', DARK_STATUS.indexOf(c.id) !== -1);

    Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (t) {
      t.classList.toggle('on', c.isTab && 's-' + t.dataset.tab === c.id);
    });
    fab.classList.toggle('on', c.id === 's-camera');
  }

  function goTab(name) {
    stack = [{ id: 's-' + name, isTab: true }];
    render();
  }

  function push(id) {
    stack.push({ id: id, isTab: false });
    render();
  }

  function back() {
    if (stack.length > 1) {
      stack.pop();
      render();
    }
  }

  /* ---------------- Toast ---------------- */
  var toastTimer = null;
  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('show'); }, 1800);
  }

  /* ---------------- 状态同步 ---------------- */
  function syncPoints() {
    var txt = state.points.toLocaleString('en-US');
    document.getElementById('homePoints').textContent = txt;
    document.getElementById('homePoints2').innerHTML = txt + '<span>积分</span>';
    document.getElementById('minePoints').innerHTML = txt + ' <svg><use href="#i-chev"/></svg>';
    document.getElementById('pointsBig').textContent = txt;
  }

  function syncSign() {
    var s1 = document.getElementById('signState');
    var s2 = document.getElementById('signState2');
    var btn = document.getElementById('signBtn');
    var tag = document.getElementById('signTag');
    if (state.signed) {
      if (s1) s1.textContent = '今日已签到 · 连续 ' + state.signStreak + ' 天';
      if (s2) s2.textContent = '连续 ' + state.signStreak + ' 天 · 今日已领';
      if (btn) { btn.textContent = '已签到'; btn.setAttribute('disabled', ''); }
      if (tag) { tag.className = 'tag tag--ok'; tag.textContent = '已领取'; }
    }
  }

  function syncUnread() {
    var badge = document.getElementById('tabBadge');
    badge.style.display = state.unread > 0 ? '' : 'none';
    badge.textContent = state.unread;
  }

  /* ---------------- 交互动作 ---------------- */
  function fillLink() {
    var box = document.getElementById('pasteBox');
    if (box.classList.contains('filled')) return;
    box.classList.add('filled');
    box.innerHTML =
      '<div class="pb-url">https://item.taobao.com/item.htm?id=7281****4&spm=a1z10.3</div>' +
      '<div class="pb-site-row">' +
        '<span class="site-chip on">淘宝</span>' +
        '<span class="site-chip">1688</span>' +
        '<span class="site-chip">天猫</span>' +
        '<span class="site-chip">Temu</span>' +
      '</div>';
    toast('已识别站点：淘宝');
  }

  function addRecord() {
    var box = document.getElementById('pasteBox');
    if (!box.classList.contains('filled')) {
      toast('请先粘贴商品链接');
      return;
    }
    var list = document.getElementById('recList');
    var el = document.createElement('div');
    el.className = 'rec new';
    el.setAttribute('data-go', 's-collect-detail');
    el.innerHTML =
      '<span class="rc-thumb"><svg><use href="#i-image"/></svg></span>' +
      '<div class="rc-body">' +
        '<div class="rc-t">新采集商品 · 待 PC 端解析标题</div>' +
        '<div class="rc-m">' +
          '<span class="tag tag--brand" data-role="st"><svg><use href="#i-clock"/></svg>已入队</span>' +
          '<span class="tag">淘宝</span>' +
          '<span class="rc-time num">刚刚</span>' +
        '</div>' +
      '</div>';
    list.insertBefore(el, list.firstChild);

    // 重置输入框
    box.classList.remove('filled');
    box.innerHTML = '<div class="pb-ph">粘贴淘宝 / 1688 / 天猫 / Temu 商品链接…</div>';
    toast('已提交采集，正在同步到 PC…');

    // 模拟同步完成
    setTimeout(function () {
      if (!el.parentNode) return;
      var st = el.querySelector('[data-role="st"]');
      st.className = 'tag tag--ok';
      st.innerHTML = '<svg><use href="#i-check"/></svg>已同步';
      toast('已同步到 PC 草稿池');
    }, 1900);
  }

  function shoot() {
    var list = document.getElementById('queueList');
    var n = Date.now().toString().slice(-4);
    var el = document.createElement('div');
    el.className = 'queue-item';
    el.innerHTML =
      '<span class="qi-thumb"><svg><use href="#i-image"/></svg><span class="prog"></span></span>' +
      '<div class="qb">' +
        '<div class="qb-t">IMG_20261008_' + n + '.jpg</div>' +
        '<div class="qb-s">已上传 · 等待 commit</div>' +
      '</div>' +
      '<span class="tag tag--brand">上传中</span>';
    list.insertBefore(el, list.firstChild);

    var prog = el.querySelector('.prog');
    var sub = el.querySelector('.qb-s');
    var tag = el.querySelector('.tag');
    var title = el.querySelector('.qb-t').textContent;

    requestAnimationFrame(function () { prog.classList.add('run'); });

    setTimeout(function () {
      prog.classList.add('done');
      sub.textContent = '中转中 · TTL 剩余 23 小时';
      tag.className = 'tag tag--brand';
      tag.textContent = '中转';
    }, 1200);

    setTimeout(function () {
      sub.className = 'qb-s ok';
      sub.textContent = '已同步 · 服务器副本已删除';
      tag.className = 'tag tag--ok';
      tag.textContent = '完成';
      toast('拍照已同步到 PC 并删除服务器副本');
    }, 2800);

    toast('已拍照：' + title);
  }

  function signin() {
    if (state.signed) { toast('今日已签到，明天再来'); return; }
    state.signed = true;
    state.signStreak += 1;
    state.points += 50;
    syncPoints();
    syncSign();

    var log = document.getElementById('signLog');
    if (log) {
      var row = document.createElement('div');
      row.className = 'ledger';
      row.innerHTML =
        '<div class="lg-b"><div class="lg-t">每日签到</div>' +
        '<div class="lg-s num">刚刚 · 连续 ' + state.signStreak + ' 天</div></div>' +
        '<span class="lg-v plus">＋50</span>';
      log.insertBefore(row, log.firstChild);
    }
    var grid = document.getElementById('signGrid');
    if (grid) grid.children[7].classList.add('today');

    toast('签到成功，＋50 积分');
  }

  function readAll() {
    if (state.unread === 0) { toast('没有未读消息'); return; }
    state.unread = 0;
    Array.prototype.forEach.call(document.querySelectorAll('.ms-unread'), function (d) { d.remove(); });
    syncUnread();
    toast('已全部标记为已读');
  }

  function updatePlanDisplay() {
    document.getElementById('planPrice').textContent = state.plan.price;
    document.getElementById('orderPlan').textContent = state.plan.name;
    document.getElementById('orderPrice').textContent = state.plan.price.replace('¥', '¥') + '.00';
  }

  /* ---------------- 事件委托 ---------------- */
  document.addEventListener('click', function (e) {
    var t, el;

    // 1) 返回
    if ((el = e.target.closest('[data-back]'))) { back(); return; }

    // 2) Tab（含评审面板、快捷动作、FAB）
    if ((el = e.target.closest('[data-tab]'))) { goTab(el.dataset.tab); return; }

    // 3) 二级页
    if ((el = e.target.closest('[data-go]'))) { push(el.dataset.go); return; }

    // 4) 分段控件
    if ((el = e.target.closest('.segment button'))) {
      var seg = el.closest('.segment');
      Array.prototype.forEach.call(seg.children, function (b) { b.classList.remove('on'); });
      el.classList.add('on');
      if (seg.id === 'inboxSeg') {
        var key = el.dataset.seg;
        document.getElementById('panel-ann').classList.toggle('on', key === 'ann');
        document.getElementById('panel-fb').classList.toggle('on', key === 'fb');
      }
      return;
    }

    // 5) 动作
    if (!(el = e.target.closest('[data-action]'))) return;
    t = el.dataset.action;

    switch (t) {
      case 'login':
        goTab('home');
        toast('登录成功');
        break;

      case 'force-login':
        goTab('home');
        toast('已强制登录，原设备会话失效（R8）');
        break;

      case 'logout':
        stack = [{ id: 's-login', isTab: false }];
        render();
        toast('已退出登录');
        break;

      case 'paste':
      case 'fill-link':
        fillLink();
        break;

      case 'collect':
        addRecord();
        break;

      case 'recollect':
        toast('已重新加入采集队列');
        break;

      case 'shoot':
        shoot();
        break;

      case 'album':
        toast('从相册选择（原型未接入）');
        break;

      case 'pick-combo':
        document.getElementById('comboName').textContent = el.dataset.name;
        back();
        toast('已归属：' + el.dataset.name);
        break;

      case 'refresh':
        toast('已刷新商品组合');
        break;

      case 'signin':
        signin();
        break;

      case 'readall':
        readAll();
        break;

      case 'pick-plan':
        Array.prototype.forEach.call(document.querySelectorAll('#planList .plan'), function (p) {
          p.classList.remove('on');
        });
        el.classList.add('on');
        state.plan = {
          name: el.dataset.name,
          price: '¥' + el.querySelector('.pl-p').textContent.replace('¥', '')
        };
        updatePlanDisplay();
        break;

      case 'pick-pay':
        var payList = el.parentNode;
        Array.prototype.forEach.call(payList.children, function (r) { r.classList.remove('on'); });
        el.classList.add('on');
        break;

      case 'pay':
        toast('订单已创建，正在调起支付宝…');
        setTimeout(function () {
          toast('等待支付结果（轮询订单状态）');
        }, 1600);
        break;

      case 'unbind':
        toast('已解绑（原型模拟）');
        break;

      case 'refresh-code':
        toast('配对码已刷新，10 分钟内有效');
        break;

      case 'switch':
      case 'switch-lan':
        var sw = el.querySelector('.switch');
        if (sw) sw.classList.toggle('on');
        toast(sw && sw.classList.contains('on') ? '已开启' : '已关闭');
        break;

      case 'copy':
      case 'copy-host':
        toast('已复制到剪贴板');
        break;

      case 'clear-cache':
        toast('本地缓存已清除');
        break;

      case 'check-update':
        toast('当前已是最新版本 v1.5.2');
        break;

      case 'save-password':
        toast('密码已更新，请在其他设备重新登录');
        break;

      case 'send':
        toast('回复已提交');
        break;

      case 'rename':
        toast('用户名修改（原型未接入）');
        break;

      case 'ad':
        toast('广告视频功能未开放（预留位）');
        break;
    }
  });

  /* ---------------- 初始化 ---------------- */
  syncUnread();
  updatePlanDisplay();
  render();
})();
