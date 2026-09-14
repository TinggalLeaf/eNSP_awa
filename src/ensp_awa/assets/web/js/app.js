/* eNSP-AWA 前端逻辑 */
(function () {
  'use strict';

  // ---------------------------------------------------------------- //
  // 工具
  // ---------------------------------------------------------------- //
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

  function toast(msg, type = '', ms = 3600) {
    const el = document.createElement('div');
    el.className = 'toast ' + type;
    el.textContent = msg;
    $('#toast-wrap').appendChild(el);
    setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 320); }, ms);
  }

  marked.setOptions({ breaks: true, gfm: true });
  function mdToHtml(text) {
    const raw = marked.parse(text || '');
    const clean = DOMPurify.sanitize(raw, { FORBID_TAGS: ['style'] });
    const tpl = document.createElement('template');
    tpl.innerHTML = clean;
    tpl.content.querySelectorAll('pre code').forEach((block) => {
      try { hljs.highlightElement(block); } catch (e) { /* noop */ }
    });
    return tpl.innerHTML;
  }

  // ---------------------------------------------------------------- //
  // Python 桥
  // ---------------------------------------------------------------- //
  let api = null;
  const py = () => api;

  window.__AWA__ = {
    emit(ev) {
      if (!ev || !ev.type) return;
      Chat.onEvent(ev);
      Settings.onEvent(ev);
    },
  };

  // ---------------------------------------------------------------- //
  // 导航 + 动画
  // ---------------------------------------------------------------- //
  function switchView(name) {
    $$('.nav-btn').forEach((b) => b.classList.toggle('active', b.dataset.view === name));
    $$('.view').forEach((v) => v.classList.remove('active'));
    const view = $('#view-' + name);
    view.classList.add('active');
    if (window.gsap) {
      gsap.fromTo(view.querySelectorAll('.panel, .chat-wrap, .view-head'),
        { opacity: 0, y: 18 }, { opacity: 1, y: 0, duration: 0.5, stagger: 0.06, ease: 'power3.out', clearProps: 'all' });
    }
    if (name === 'devices') Devices.refresh();
    if (name === 'settings') Settings.render();
  }

  function initParticles() {
    if (!window.tsParticles) return;
    tsParticles.load('bg-particles', {
      fpsLimit: 60,
      particles: {
        number: { value: 72, density: { enable: true, area: 900 } },
        color: { value: ['#22d3ee', '#8b5cf6', '#38bdf8'] },
        links: { enable: true, distance: 130, color: '#3b82f6', opacity: 0.28, width: 1 },
        move: { enable: true, speed: 0.7, outModes: { default: 'bounce' } },
        size: { value: { min: 1, max: 2.6 } },
        opacity: { value: 0.65 },
      },
      interactivity: {
        events: { onHover: { enable: true, mode: 'grab' }, onClick: { enable: true, mode: 'push' } },
        modes: { grab: { distance: 150, links: { opacity: 0.5 } }, push: { quantity: 2 } },
      },
      detectRetina: true,
    }).catch(() => {});
  }

  function animateIn() {
    if (!window.gsap) return;
    gsap.from('#sidebar', { x: -60, opacity: 0, duration: 0.7, ease: 'power3.out' });
    gsap.from('.view-head', { y: -24, opacity: 0, duration: 0.6, delay: 0.15, ease: 'power3.out', clearProps: 'all' });
    gsap.from('#view-chat .chat-wrap', { y: 26, opacity: 0, duration: 0.6, delay: 0.25, ease: 'power3.out', clearProps: 'all' });
  }

  // ---------------------------------------------------------------- //
  // 聊天视图
  // ---------------------------------------------------------------- //
  const Chat = {
    busy: false,
    curAssistant: null,   // {bubble, textEl, text, reasonEl, reasonText, chips}
    streaming: false,

    init() {
      $('#btn-send').onclick = () => this.send();
      $('#btn-stop').onclick = () => py().chat_stop();
      $('#btn-clear-chat').onclick = async () => { await py().chat_clear(); $('#chat-messages').innerHTML = ''; toast('对话已清空', 'ok'); };
      const inp = $('#chat-input');
      inp.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.send(); }
      });
      py().chat_history().then((r) => {
        (r.history || []).filter((m) => m.role !== 'tool').forEach((m) => this.renderMessage(m));
      }).catch(() => {});
    },

    setBusy(b) {
      this.busy = b;
      $('#btn-send').disabled = b;
      $('#btn-stop').disabled = !b;
    },

    send() {
      const inp = $('#chat-input');
      const text = inp.value.trim();
      if (!text || this.busy) return;
      inp.value = '';
      this.renderMessage({ role: 'user', content: text });
      this.setBusy(true);
      py().chat_send(text).then((r) => {
        if (!r.ok) { this.setBusy(false); toast(r.error || '发送失败', 'err'); }
      }).catch((e) => { this.setBusy(false); toast(String(e), 'err'); });
    },

    renderMessage(m) {
      const wrap = $('#chat-messages');
      const div = document.createElement('div');
      div.className = 'msg ' + (m.role === 'user' ? 'user' : 'assistant');
      const who = m.role === 'user' ? '你' : 'AI';
      let inner = `<div class="who">${who}</div><div class="bubble">`;
      if (m.reasoning) {
        inner += `<details class="reasoning" ${m.role === 'assistant' ? '' : 'open'}><summary>💭 思考过程</summary><div>${esc(m.reasoning)}</div></details>`;
      }
      if (m.content) inner += mdToHtml(m.content);
      if (m.tool_calls && m.tool_calls.length) {
        inner += '<div>' + m.tool_calls.map((tc) =>
          `<span class="toolchip">⚙ ${esc(tc.name)}</span>`).join('') + '</div>';
      }
      inner += '</div>';
      div.innerHTML = inner;
      wrap.appendChild(div);
      wrap.scrollTop = wrap.scrollHeight;
      return div;
    },

    startAssistant() {
      const wrap = $('#chat-messages');
      const div = document.createElement('div');
      div.className = 'msg assistant';
      div.innerHTML = `<div class="who">AI</div><div class="bubble">
        <div class="reasoning" hidden><summary>💭 思考过程</summary><div class="r-body"></div></div>
        <div class="body"></div><div class="chips"></div><span class="cursor"></span></div>`;
      wrap.appendChild(div);
      wrap.scrollTop = wrap.scrollHeight;
      this.curAssistant = {
        el: div,
        body: div.querySelector('.body'),
        chips: div.querySelector('.chips'),
        reason: div.querySelector('.reasoning'),
        reasonBody: div.querySelector('.r-body'),
        text: '',
        reasonText: '',
      };
      this.streaming = true;
    },

    onEvent(ev) {
      if (ev.type === 'text') {
        if (!this.curAssistant) this.startAssistant();
        const c = this.curAssistant;
        c.text += ev.delta;
        c.body.innerHTML = mdToHtml(c.text);
        this.scrollBottom();
      } else if (ev.type === 'reasoning') {
        if (!this.curAssistant) this.startAssistant();
        const c = this.curAssistant;
        c.reason.hidden = false;
        c.reasonText += ev.delta;
        c.reasonBody.textContent = c.reasonText;
        this.scrollBottom();
      } else if (ev.type === 'tool_start') {
        if (!this.curAssistant) this.startAssistant();
        const chip = document.createElement('span');
        chip.className = 'toolchip run';
        chip.dataset.tool = ev.name;
        chip.title = JSON.stringify(ev.args || {});
        chip.innerHTML = `<span class="dot"></span>⚙ ${esc(ev.name)}`;
        this.curAssistant.chips.appendChild(chip);
        this.scrollBottom();
      } else if (ev.type === 'tool_end') {
        if (!this.curAssistant) return;
        const chip = this.curAssistant.chips.querySelector(`[data-tool="${ev.name}"]`);
        if (chip) {
          chip.classList.remove('run');
          if (!ev.ok) chip.classList.add('err');
          chip.innerHTML = `${ev.ok ? '✓' : '✗'} ⚙ ${esc(ev.name)} · ${esc((ev.summary || '').slice(0, 80))}`;
          chip.title = ev.summary || '';
        }
        this.scrollBottom();
      } else if (ev.type === 'error') {
        this.stopCursor();
        toast(ev.message || '出错了', 'err', 5200);
      } else if (ev.type === 'cancelled') {
        this.stopCursor();
        toast('已停止', '');
      } else if (ev.type === 'done' || ev.type === 'turn_end') {
        this.stopCursor();
        this.setBusy(false);
      }
    },

    stopCursor() {
      if (this.curAssistant) {
        const cur = this.curAssistant.el.querySelector('.cursor');
        if (cur) cur.remove();
      }
      this.streaming = false;
    },

    scrollBottom() {
      const wrap = $('#chat-messages');
      wrap.scrollTop = wrap.scrollHeight;
    },
  };

  // ---------------------------------------------------------------- //
  // 拓扑工坊
  // ---------------------------------------------------------------- //
  const MODELS = ['AR1220', 'AR201', 'AR2220', 'AR2240', 'S2700', 'S3700', 'S5700', 'S6700',
    'AC6005', 'AP6050', 'USG5500', 'USG6000V', 'PC', 'STA', 'Laptop', 'MCS'];
  const DEV_ICONS = {
    AR: 'M6 30 L24 10 L42 30 M14 30 V44 H34 V30',             // 路由器
    USG: 'M10 12 H38 V40 H10 Z M10 20 H38 M18 12 V40',        // 防火墙
    S: 'M12 14 H36 M12 24 H36 M12 34 H36 M12 44 H36',         // 交换机
    AC: 'M24 16 A12 12 0 0 1 36 28 M24 16 A12 12 0 0 0 12 28 M24 22 V40 M18 46 H30', // AC
    AP: 'M24 14 A14 14 0 0 1 38 28 M24 14 A14 14 0 0 0 10 28 M24 24 V38 M20 44 H28', // AP
    PC: 'M10 12 H38 V32 H10 Z M18 38 H30 M24 32 V38',         // PC
  };

  const Topo = {
    modelsRef: null,

    async init() {
      this.addDev(); this.addDev(); this.addDev(); this.addLink();
      $('#btn-add-dev').onclick = () => this.addDev();
      $('#btn-add-link').onclick = () => this.addLink();
      $('#btn-gen-topo').onclick = () => this.generate();
      $('#btn-pick-topo').onclick = async () => {
        const p = await py().pick_file();
        if (p) this.parse(p);
      };
      $$('#view-topo .tab').forEach((t) => t.onclick = () => {
        $$('#view-topo .tab').forEach((x) => x.classList.toggle('active', x === t));
        $$('#view-topo .tab-page').forEach((x) => x.classList.toggle('active', x.id === 'tab-' + t.dataset.tab));
      });
      const r = await py().topo_models();
      this.modelsRef = r;
      this.refreshCanvas();
    },

    addDev(name = '', model = 'AR2220') {
      const tb = $('#dev-table tbody');
      const tr = document.createElement('tr');
      tr.innerHTML = `<td><input class="d-name" value="${esc(name)}" placeholder="如: 核心路由器"></td>
        <td><select class="d-model">${MODELS.map((m) => `<option ${m === model ? 'selected' : ''}>${m}</option>`).join('')}</select></td>
        <td><button class="del" title="删除">×</button></td>`;
      tr.querySelector('.del').onclick = () => { tr.remove(); this.refreshCanvas(); };
      tr.querySelectorAll('input,select').forEach((el) => el.oninput = () => this.refreshCanvas());
      tb.appendChild(tr);
      if (window.gsap) gsap.from(tr, { opacity: 0, x: -14, duration: 0.3, clearProps: 'all' });
      this.refreshLinkSelects();
      return tr;
    },

    addLink() {
      const tb = $('#link-table tbody');
      const tr = document.createElement('tr');
      tr.innerHTML = `<td><select class="l-src"></select></td>
        <td><input class="l-sif" placeholder="GE0/0/0" spellcheck="false"></td>
        <td><select class="l-dst"></select></td>
        <td><input class="l-dif" placeholder="GE0/0/1" spellcheck="false"></td>
        <td><button class="del" title="删除">×</button></td>`;
      tr.querySelector('.del').onclick = () => tr.remove();
      tb.appendChild(tr);
      this.refreshLinkSelects();
      if (window.gsap) gsap.from(tr, { opacity: 0, x: -14, duration: 0.3, clearProps: 'all' });
      return tr;
    },

    devNames() {
      return $$('#dev-table tbody tr').map((tr) => tr.querySelector('.d-name').value.trim()).filter(Boolean);
    },

    refreshLinkSelects() {
      const names = this.devNames();
      $$('#link-table tbody tr').forEach((tr) => {
        ['.l-src', '.l-dst'].forEach((cls) => {
          const sel = tr.querySelector(cls);
          const prev = sel.value;
          sel.innerHTML = names.map((n) => `<option>${esc(n)}</option>`).join('');
          if (names.includes(prev)) sel.value = prev;
        });
      });
    },

    collectSpec() {
      const devices = $$('#dev-table tbody tr').map((tr, i) => ({
        name: tr.querySelector('.d-name').value.trim(),
        model: tr.querySelector('.d-model').value,
        _row: i,
      })).filter((d) => d.name);
      const pos = this.layoutPositions(devices);
      devices.forEach((d, i) => { d.cx = pos[i].x; d.cy = pos[i].y; });
      const names = new Set(devices.map((d) => d.name));
      const links = $$('#link-table tbody tr').map((tr) => ({
        src: tr.querySelector('.l-src').value,
        dst: tr.querySelector('.l-dst').value,
        src_iface: tr.querySelector('.l-sif').value.trim(),
        dst_iface: tr.querySelector('.l-dif').value.trim(),
      })).filter((l) => l.src && l.dst && names.has(l.src) && names.has(l.dst));
      return { devices, links };
    },

    tier(model) {
      const m = model.toUpperCase();
      if (m.startsWith('AR') || m.startsWith('USG')) return 0;
      if (m.startsWith('S57') || m.startsWith('S67') || m.startsWith('AC')) return 1;
      if (m.startsWith('S3') || m.startsWith('S2')) return 2;
      return 3;
    },

    layoutPositions(devices) {
      // 与后端一致的简单分层
      const tiers = {};
      devices.forEach((d) => { (tiers[this.tier(d.model)] ||= []).push(d); });
      const order = Object.keys(tiers).sort();
      const W = 1200, H = 800, top = 90, bot = 90;
      const band = (H - top - bot) / Math.max(order.length, 1);
      const pos = new Array(devices.length);
      order.forEach((t, row) => {
        const g = tiers[t];
        const gap = W / (g.length + 1);
        g.forEach((d, i) => { pos[d._row] = { x: gap * (i + 1), y: top + band * row + band / 2 }; });
      });
      return pos;
    },

    iconFor(model) {
      const m = model.toUpperCase();
      if (m.startsWith('AR')) return DEV_ICONS.AR;
      if (m.startsWith('USG')) return DEV_ICONS.USG;
      if (m.startsWith('S')) return DEV_ICONS.S;
      if (m.startsWith('AC')) return DEV_ICONS.AC;
      if (m.startsWith('AP')) return DEV_ICONS.AP;
      return DEV_ICONS.PC;
    },

    refreshCanvas() {
      const spec = this.collectSpec ? this.peekSpec() : { devices: [], links: [] };
      this.drawCanvas(spec);
    },

    peekSpec() {
      const devices = $$('#dev-table tbody tr').map((tr, i) => ({
        name: tr.querySelector('.d-name').value.trim(),
        model: tr.querySelector('.d-model').value,
        _row: i,
      })).filter((d) => d.name);
      const pos = this.layoutPositions(devices);
      devices.forEach((d, i) => { d.cx = pos[i].x; d.cy = pos[i].y; });
      const names = new Set(devices.map((d) => d.name));
      const links = $$('#link-table tbody tr').map((tr) => ({
        src: tr.querySelector('.l-src').value,
        dst: tr.querySelector('.l-dst').value,
      })).filter((l) => l.src && l.dst && names.has(l.src) && names.has(l.dst));
      return { devices, links };
    },

    drawCanvas(spec) {
      const svg = $('#topo-canvas');
      const byName = {};
      let html = '<defs><linearGradient id="ng" x1="0" y1="0" x2="1" y2="1">' +
        '<stop offset="0" stop-color="#22d3ee"/><stop offset="1" stop-color="#8b5cf6"/></linearGradient></defs>';
      html += '<g stroke="rgba(120,150,220,0.25)" stroke-width="1">';
      for (let x = 0; x <= 1200; x += 60) html += `<line x1="${x}" y1="0" x2="${x}" y2="800"/>`;
      for (let y = 0; y <= 800; y += 60) html += `<line x1="0" y1="${y}" x2="1200" y2="${y}"/>`;
      html += '</g>';
      spec.links.forEach((l) => {
        const a = spec.devices.find((d) => d.name === l.src);
        const b = spec.devices.find((d) => d.name === l.dst);
        if (!a || !b) return;
        html += `<line x1="${a.cx}" y1="${a.cy}" x2="${b.cx}" y2="${b.cy}" stroke="url(#ng)" stroke-width="2" stroke-dasharray="6 5" opacity="0.85">
          <animate attributeName="stroke-dashoffset" from="44" to="0" dur="1.6s" repeatCount="indefinite"/></line>`;
      });
      spec.devices.forEach((d) => {
        byName[d.name] = d;
        const isNet = !/^(PC|STA|LAPTOP|MCS|AP)/i.test(d.model);
        html += `<g class="dev-node" transform="translate(${d.cx - 26},${d.cy - 26})">
          <rect width="52" height="52" rx="12" fill="rgba(17,25,46,0.92)" stroke="url(#ng)" stroke-width="1.6"/>
          <path d="${this.iconFor(d.model)}" transform="translate(4,1) scale(0.95)" fill="none" stroke="${isNet ? '#22d3ee' : '#34d399'}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>
          <text class="dev-label" x="26" y="68" text-anchor="middle">${esc(d.name)}</text>
          <text class="dev-model" x="26" y="82" text-anchor="middle">${esc(d.model)}</text>
        </g>`;
      });
      svg.innerHTML = html;
    },

    async generate() {
      const spec = this.collectSpec();
      if (!spec.devices.length) { toast('请先添加设备', 'err'); return; }
      const btn = $('#btn-gen-topo');
      btn.disabled = true; btn.textContent = '⏳ 生成中…';
      try {
        const r = await py().topo_generate({
          filename: $('#topo-filename').value.trim() || 'topology.topo',
          devices: spec.devices, links: spec.links,
        });
        const box = $('#gen-result');
        if (r.ok) {
          const checks = (r.checks || []).map((c) =>
            `<div class="${c.ok ? 'ok' : 'fail'}">${c.ok ? '✓' : '✗'} ${esc(c.name)} <span class="dim">${esc(c.detail || '')}</span></div>`).join('');
          box.innerHTML = `<div class="ok">✓ 已生成（GBK 落盘，${r.size} 字节）</div>
            <div class="dim path">${esc(r.path)}</div>${checks}
            <div class="dim">设备: ${r.devices.map((d) => esc(d.name)).join('、')}</div>`;
          toast('拓扑已生成', 'ok');
        } else {
          box.innerHTML = `<div class="fail">✗ 生成失败</div><div class="dim">${esc((r.errors || [r.error || '未知错误']).join('\n'))}</div>`;
          toast('生成失败', 'err');
        }
        if (window.gsap) gsap.from(box.children, { opacity: 0, y: 8, stagger: 0.05, duration: 0.35, clearProps: 'all' });
      } catch (e) {
        toast(String(e), 'err');
      } finally {
        btn.disabled = false; btn.textContent = '⚡ 生成 .topo（GBK 落盘）';
      }
    },

    async parse(path) {
      $('#parse-path').textContent = path;
      const box = $('#parse-result');
      box.innerHTML = '<p class="dim pad">解析中…</p>';
      const r = await py().topo_parse(path);
      if (!r.ok) { box.innerHTML = `<p class="fail">✗ ${esc(r.error)}</p>`; return; }
      const s = r.summary;
      let html = `<h3 class="pad">设备清单（${s.devices.length} 台）</h3>
        <table class="data"><tr><th>设备名</th><th>型号</th><th>Console 端口</th></tr>` +
        s.devices.map((d) => `<tr><td>${esc(d.name)}</td><td>${esc(d.model)}</td><td>${d.com_port}</td></tr>`).join('') +
        `</table><h3 class="pad">连线清单（${s.links.length} 条）</h3>
        <table class="data"><tr><th>源</th><th>源 index</th><th>目标</th><th>目标 index</th><th>介质</th></tr>` +
        s.links.map((l) => `<tr><td>${esc(l.src)}</td><td>${l.src_index}</td><td>${esc(l.dst)}</td><td>${l.tar_index}</td><td>${esc(l.type)}</td></tr>`).join('') + '</table>';
      const probe = await py().topo_validate_text(path);
      if (probe.ok) {
        html += `<h3 class="pad">GBK 回读校验</h3><p class="ok">✓ 设备名回读正常：${probe.device_names.map(esc).join('、')}</p>`;
      }
      box.innerHTML = html;
    },
  };

  // ---------------------------------------------------------------- //
  // 设备终端
  // ---------------------------------------------------------------- //
  const QUICK_CMDS = ['display version', 'display ip interface brief', 'display ip routing-table',
    'display current-configuration', 'display vlan', 'display ospf peer brief', 'display lldp neighbor brief'];

  const Devices = {
    selected: null,

    init() {
      $('#btn-scan').onclick = () => this.scan();
      $('#btn-connect').onclick = () => this.connect();
      $('#btn-refresh-sessions').onclick = () => this.refresh();
      $('#btn-disconnect-all').onclick = async () => { await py().dev_disconnect_all(); toast('已全部断开'); this.refresh(); };
      $('#btn-send-cmd').onclick = () => this.sendCmd();
      $('#btn-save-cfg').onclick = () => this.saveCfg();
      $('#btn-send-batch').onclick = () => this.sendBatch();
      $('#cmd-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') this.sendCmd(); });
      const qc = $('#quick-cmds');
      QUICK_CMDS.forEach((c) => {
        const b = document.createElement('button');
        b.className = 'btn'; b.textContent = c;
        b.onclick = () => { $('#cmd-input').value = c; this.sendCmd(); };
        qc.appendChild(b);
      });
    },

    async scan() {
      const box = $('#scan-result');
      box.textContent = '扫描中…';
      const r = await py().dev_scan(+$('#scan-start').value || 2000, +$('#scan-end').value || 2100);
      if (!r.ok) { box.textContent = '扫描失败: ' + (r.error || ''); return; }
      if (!r.ports.length) { box.textContent = '未发现可达端口（请先在 eNSP 中启动设备）'; return; }
      box.innerHTML = '可达端口: ' + r.ports.map((p) =>
        `<button class="btn mini conn-suggest" data-port="${p.port}">${p.port} 连接</button>`).join(' ');
      $$('.conn-suggest').forEach((b) => b.onclick = () => {
        $('#conn-port').value = b.dataset.port;
        $('#conn-name').value = 'device-' + b.dataset.port;
        this.connect();
      });
    },

    async connect() {
      const name = $('#conn-name').value.trim();
      const port = +$('#conn-port').value;
      if (!name || !port) { toast('请填写会话名和端口', 'err'); return; }
      toast(`正在连接 ${name}…`);
      const r = await py().dev_connect(name, port);
      if (r.ok) { toast(`${name} 已连接`, 'ok'); this.selected = name; }
      else toast(r.error || '连接失败', 'err');
      this.refresh();
    },

    async refresh() {
      const r = await py().dev_sessions();
      const list = $('#session-list');
      const sessions = r.sessions || [];
      $('#stat-sessions').textContent = '会话: ' + sessions.filter((s) => s.status === 'active').length;
      if (!sessions.length) { list.innerHTML = '<p class="dim">暂无会话。</p>'; return; }
      list.innerHTML = '';
      sessions.forEach((s) => {
        const card = document.createElement('div');
        card.className = 'session-card' + (this.selected === s.name ? ' sel' : '');
        const st = (s.status || '').toLowerCase();
        card.innerHTML = `<div><div class="sname">${esc(s.name)}</div>
          <div class="smeta">${esc(s.host || '')}:${s.port} · 闲置 ${Math.round(s.idle_seconds || 0)}s</div></div>
          <span class="st ${st}">${esc(s.status || '')}</span>`;
        card.onclick = () => { this.selected = s.name; this.refresh(); this.setConsoleTitle(); };
        list.appendChild(card);
      });
    },

    setConsoleTitle() {
      $('#console-title').textContent = this.selected || '';
    },

    print(device, cmd, output, errored) {
      const out = $('#console-out');
      if (out.dataset.placeholder) { out.textContent = ''; delete out.dataset.placeholder; }
      const line = document.createElement('div');
      line.innerHTML = `<span class="c-cmd">&lt;${esc(device)}&gt; ${esc(cmd)}</span>\n<span class="${errored ? 'c-err' : ''}">${esc(output || '')}</span>`;
      out.appendChild(line);
      out.scrollTop = out.scrollHeight;
    },

    async sendCmd() {
      if (!this.selected) { toast('请先选择会话', 'err'); return; }
      const inp = $('#cmd-input');
      const cmd = inp.value.trim();
      if (!cmd) return;
      inp.value = '';
      this.print(this.selected, cmd, '…');
      const r = await py().dev_command(this.selected, cmd);
      const out = $('#console-out');
      out.lastChild.remove();
      if (r.ok) this.print(r.device, r.command, r.output, r.errored);
      else this.print(this.selected, cmd, 'ERROR: ' + r.error, true);
    },

    async saveCfg() {
      if (!this.selected) { toast('请先选择会话', 'err'); return; }
      toast('保存配置中…');
      const r = await py().dev_save(this.selected);
      if (r.ok) { (r.results || []).forEach((x) => this.print(r.device, x.command, x.output, x.errored)); toast('配置已保存', 'ok'); }
      else toast(r.error, 'err');
    },

    async sendBatch() {
      if (!this.selected) { toast('请先选择会话', 'err'); return; }
      const lines = $('#batch-input').value.split('\n').map((l) => l.trim()).filter(Boolean);
      if (!lines.length) return;
      toast(`批量下发 ${lines.length} 条…`);
      const r = await py().dev_commands(this.selected, lines);
      if (r.ok) { (r.results || []).forEach((x) => this.print(r.device, x.command, x.output, x.errored)); toast('批量下发完成', 'ok'); }
      else toast(r.error, 'err');
    },
  };

  // ---------------------------------------------------------------- //
  // 实验报告
  // ---------------------------------------------------------------- //
  const Report = {
    init() {
      $('#btn-pick-rep-topo').onclick = async () => {
        const p = await py().pick_file();
        if (p) $('#rep-topo').value = p;
      };
      $('#btn-gen-report').onclick = () => this.generate();
    },

    async generate() {
      const btn = $('#btn-gen-report');
      btn.disabled = true; btn.textContent = '⏳ 采集中…';
      const box = $('#rep-result');
      box.innerHTML = '<p class="dim">正在采集设备配置与 display 输出…</p>';
      try {
        const devices = $('#rep-devices').value.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
        const r = await py().report_generate(
          $('#rep-title').value.trim() || 'eNSP 网络实验报告',
          $('#rep-topo').value.trim(),
          devices.length ? devices : null,
          $('#rep-pdf').checked
        );
        if (r.ok) {
          box.innerHTML = `<div class="ok">✓ 报告已生成</div>
            <div class="dim path">Markdown: ${esc(r.markdown_path)}</div>
            ${r.pdf ? `<div class="dim path">PDF: ${esc(r.pdf)}</div>` : ''}`;
          toast('实验报告已生成', 'ok');
        } else {
          box.innerHTML = `<div class="fail">✗ ${esc(r.error || '生成失败')}</div>`;
          toast('报告生成失败', 'err');
        }
      } catch (e) {
        toast(String(e), 'err');
      } finally {
        btn.disabled = false; btn.textContent = '▤ 生成实验报告';
      }
    },
  };

  // ---------------------------------------------------------------- //
  // 设置
  // ---------------------------------------------------------------- //
  const Settings = {
    cfg: null,

    async render() {
      const r = await py().get_config();
      this.cfg = r.config;
      const wrap = $('#profile-cards');
      wrap.innerHTML = '';
      (this.cfg.profiles || []).forEach((p, i) => {
        const card = document.createElement('div');
        card.className = 'profile-card' + (i === this.cfg.active_profile ? ' active' : '');
        card.innerHTML = `
          <div class="pc-head"><b>${esc(p.name || '接口 ' + (i + 1))}</b>
            <span class="fmt-badge">${esc(p.format)}</span></div>
          <label class="fld">名称 <input data-k="name" value="${esc(p.name || '')}"></label>
          <label class="fld">Base URL <input data-k="base_url" value="${esc(p.base_url || '')}" placeholder="https://api.openai.com/v1" spellcheck="false"></label>
          <label class="fld">API Key <input data-k="api_key" type="password" value="${esc(p.api_key || '')}" spellcheck="false"></label>
          <label class="fld">模型 <input data-k="model" value="${esc(p.model || '')}" placeholder="gpt-4o-mini"></label>
          <div class="row gap">
            <label class="fld grow">兼容格式
              <select data-k="format">
                <option value="chat_completions" ${p.format === 'chat_completions' ? 'selected' : ''}>Chat Completions (/chat/completions)</option>
                <option value="responses" ${p.format === 'responses' ? 'selected' : ''}>Responses (/responses)</option>
              </select></label>
            <label class="fld" style="width:110px">温度 <input data-k="temperature" type="number" step="0.1" min="0" max="2" value="${p.temperature}"></label>
            <label class="fld" style="width:120px">Max Tokens <input data-k="max_tokens" type="number" step="128" value="${p.max_tokens}"></label>
          </div>
          <div class="row gap">
            <button class="btn mini act-profile">设为当前接口</button>
            <button class="btn mini ghost test-profile">测试连通</button>
          </div>`;
        card.querySelectorAll('[data-k]').forEach((el) => {
          el.onchange = () => { p[el.dataset.k] = el.type === 'number' ? +el.value : el.value; };
        });
        card.querySelector('.act-profile').onclick = async () => {
          await this.save();
          await py().save_config({ active_profile: i });
          await this.render();
          updateStats();
          toast(`已切换到 ${p.name || '接口 ' + (i + 1)}`, 'ok');
        };
        card.querySelector('.test-profile').onclick = async () => {
          await this.save();
          await py().save_config({ active_profile: i });
          await py().test_connection();
          toast('测试中…');
        };
        wrap.appendChild(card);
      });
      $('#set-sys').value = this.cfg.system_prompt || '';
      $('#set-topo-dir').value = (this.cfg.topo || {}).output_dir || '';
      $('#set-rep-dir').value = (this.cfg.report || {}).output_dir || '';
      $('#set-author').value = (this.cfg.report || {}).author || '';
      $('#chat-format-chip').textContent = (this.cfg.profiles || [])[this.cfg.active_profile || 0]?.format || 'chat_completions';
    },

    async save() {
      const profiles = this.cfg.profiles;
      await py().save_config({
        profiles,
        system_prompt: $('#set-sys').value,
        topo: { output_dir: $('#set-topo-dir').value.trim() },
        report: { output_dir: $('#set-rep-dir').value.trim(), author: $('#set-author').value.trim() },
      });
    },

    init() {
      $('#btn-save-settings').onclick = async () => {
        await this.save();
        $('#save-tip').textContent = '已保存 ' + new Date().toLocaleTimeString();
        toast('设置已保存', 'ok');
      };
      $$('[data-pick]').forEach((b) => b.onclick = async () => {
        const d = await py().pick_dir();
        if (!d) return;
        if (b.dataset.pick === 'topo') $('#set-topo-dir').value = d;
        else $('#set-rep-dir').value = d;
      });
    },

    onEvent(ev) {
      if (ev.type === 'test_result') {
        if (ev.ok) toast(`✓ 连通正常（${ev.model || 'ok'}）`, 'ok', 5000);
        else toast('✗ ' + (ev.error || '连接失败'), 'err', 6000);
      }
    },
  };

  function updateStats() {
    py().get_config().then((r) => {
      const p = (r.config.profiles || [])[r.config.active_profile || 0];
      $('#stat-model').textContent = '模型: ' + (p && p.model ? p.model : '未配置');
    }).catch(() => {});
  }

  // ---------------------------------------------------------------- //
  // 启动
  // ---------------------------------------------------------------- //
  window.addEventListener('pywebviewready', async () => {
    api = window.pywebview.api;
    $$('.nav-btn').forEach((b) => b.onclick = () => switchView(b.dataset.view));
    Chat.init();
    await Topo.init();
    Devices.init();
    Report.init();
    Settings.init();
    await Settings.render();
    updateStats();
    initParticles();
    animateIn();
  });
})();
