/* The focused transaction-review workflow (#/review). One unresolved transaction at a time — merchant, amount,
 * account, the AI's note, a category picker — with "Just this one" and "Always categorize this merchant". A rule
 * ("Always…") always asks for an explicit confirmation first; nothing is written until then. Saving advances to the
 * next item. It reuses /api/transactions?kind=review to read and /api/transactions/category to write (make_rule the
 * only difference between the two buttons), so every number and every write stays on the one source. Loaded before
 * app.js; every bank/user string goes in as text (h()), never as markup.
 *
 * The read is capped (limit 200), so a batch is only part of the queue. Completion is never declared from the batch
 * alone: when the in-memory batch is exhausted the queue is refetched, and only an empty refetch (ignoring the rows
 * the user chose to skip this session) ends the workflow. Skipping is not resolution — a skipped row stays unresolved
 * and never counts as reviewed. */
(window.HF_EXT = window.HF_EXT || []).push((X) => {
  'use strict';
  const {$, h, fill, icon, glyph, plural, signed, money, api, post, S, PAGES, RENDER, HOOKS, card, small, skeleton,
    errorCard, toast, avatar, badge, nameMask, ensureCategories, loadCounts, route, RGB, askAboutBtn, askMonth} = X;

  PAGES.review = 'Review';
  const LIMIT = 200;   // the read cap; the queue can be longer, so completion is confirmed by a refetch, not the batch
  const ST = {rows: [], idx: 0, cats: [], sel: null, confirming: false, busy: false, resolved: 0,
    skipped: new Set(), tk: null};

  async function renderReview(tk) {
    const page = $('page');
    fill(page, skeleton(['card']));
    let data, cats;
    try {
      [data, cats] = await Promise.all([api('/api/transactions', {kind: 'review', limit: LIMIT}), ensureCategories()]);
    } catch (e) {
      if (tk === S.token) fill(page, errorCard(e, () => route()));
      return;
    }
    if (tk !== S.token) return;
    ST.rows = (data.rows || []).slice();
    ST.cats = cats.map((c) => c.name);
    ST.idx = 0;
    ST.confirming = false;
    ST.busy = false;
    ST.resolved = 0;              // a fresh render starts a new session: nothing resolved, nothing skipped yet
    ST.skipped = new Set();
    ST.tk = tk;
    draw();
  }

  // The batch is exhausted (every visible row was resolved or skipped). Refetch the queue before declaring completion:
  // a capped read may have hidden more unresolved rows, and resolving the batch could have uncovered them. Rows the
  // user skipped this session are left out so they do not loop straight back; they stay unresolved for next time.
  async function advance() {
    const page = $('page');
    const tk = ST.tk;
    fill(page, skeleton(['card']));
    let data;
    try {
      data = await api('/api/transactions', {kind: 'review', limit: LIMIT});
    } catch (e) {
      if (tk === S.token) fill(page, errorCard(e, () => advance()));
      return;
    }
    if (tk !== S.token) return;
    const fresh = (data.rows || []).filter((r) => !ST.skipped.has(r.transaction_id));
    if (fresh.length) {
      ST.rows = fresh;
      ST.idx = 0;
      ST.confirming = false;
      draw();
      return;
    }
    // Nothing unresolved remains except rows the user skipped. "All caught up" is only true when none were skipped.
    fill(page, doneCard(ST.resolved, ST.skipped.size));
  }

  function doneCard(resolved, skipped) {
    let line;
    if (skipped > 0) {
      // Skipping is never resolution: say what is still waiting, never "every transaction is reviewed".
      line = resolved > 0
        ? `Reviewed ${resolved}. ${plural(skipped, 'transaction')} skipped — still waiting for you.`
        : `${plural(skipped, 'transaction')} skipped — still waiting for you. Nothing else to review right now.`;
    } else {
      line = resolved > 0 ? 'All caught up — every transaction is reviewed.' : 'Nothing to review right now.';
    }
    const cleared = skipped === 0 && resolved > 0;
    return card({title: 'Transaction review', icon: cleared ? 'checkc' : 'swap', rgb: cleared ? RGB.ok : RGB.acc},
      h('div', {class: 'rv-done'}, glyph(cleared ? 'check' : 'swap', 2.4),
        h('p', {}, line),
        h('a', {class: 'btn', href: '#/transactions'}, 'See all transactions')));
  }

  function draw() {
    const page = $('page');
    const total = ST.rows.length;
    if (!total || ST.idx >= total) {
      advance();   // batch exhausted: confirm against the server before claiming completion
      return;
    }
    const t = ST.rows[ST.idx];
    ST.sel = t.category;
    ST.confirming = false;
    const names = ST.cats.includes(t.category) ? ST.cats : [...ST.cats, t.category];

    const picker = HOOKS.catPick({value: t.category, names, label: `Category for ${t.merchant || 'this transaction'}`},
      (name) => { ST.sel = name; ST.confirming = false; paint(); });

    const facts = h('dl', {class: 'rv-facts'},
      fact('Account', nameMask(t.account_name || 'Account', t.account_mask, {pre: t.institution_name ? `${t.institution_name} · ` : ''})),
      t.ai_note ? fact('AI note', h('span', {}, t.ai_note)) : null,
      t.pending ? fact('Status', h('span', {class: 'pend'}, 'pending')) : null);

    const actions = h('div', {class: 'rv-actions'});
    const head = h('div', {class: 'rv-top'},
      small(`${Math.min(ST.idx + 1, total)} of ${total} to review`),
      h('button', {class: 'rv-skip', type: 'button', onclick: skip}, 'Skip', icon('chevr')));
    const bar = h('div', {class: 'rv-bar', role: 'img', 'aria-label': `${ST.idx} of ${total} reviewed`},
      h('i', {style: {width: `${(ST.idx / total) * 100}%`}}));

    const body = h('div', {class: 'rv-card'},
      h('div', {class: 'rv-id'}, avatar(t.merchant, t.logo_url, t.icon_url, 44),
        h('div', {class: 'grow'}, h('b', {class: 'rv-m'}, t.merchant || t.name || '—'),
          h('span', {class: 'rv-sub'}, badge(t.category, 'xs'), ' ', `${t.category} · ${fmtDate(t.date)}`)),
        h('div', {class: `rv-amt v${t.amount < 0 ? ' pos' : ''}`}, signed(t.amount))),
      facts,
      h('div', {class: 'rv-pick'}, h('span', {class: 'rv-lab'}, 'Category'), picker,
        askAboutBtn(`Why is my ${t.merchant || 'this'} charge of ${money(Math.abs(t.amount))} on ${t.date} categorized as ${t.category}, in ${askMonth()}?`)),
      actions);

    function paint() {
      fill(actions, ST.confirming ? confirmRow() : buttonRow());
    }
    function buttonRow() {
      return [
        h('button', {class: 'btn', type: 'button', disabled: ST.busy, onclick: () => save(false)},
          icon('check'), 'Just this one'),
        h('button', {class: 'btn ghost', type: 'button', disabled: ST.busy, onclick: () => { ST.confirming = true; paint(); }},
          icon('repeat'), 'Always categorize this merchant'),
      ];
    }
    function confirmRow() {
      // Explicit confirmation: a rule applies to past and future transactions, so it never writes on the first tap.
      return h('div', {class: 'rv-confirm'},
        h('p', {}, 'Always file ', h('b', {}, t.merchant || t.name || 'this merchant'), ' under ', h('b', {}, ST.sel),
          '? This changes matching past transactions and every future one.'),
        h('div', {class: 'rv-confirm-btns'},
          h('button', {class: 'btn', type: 'button', disabled: ST.busy, onclick: () => save(true)}, icon('check'), 'Create rule'),
          h('button', {class: 'btn ghost', type: 'button', disabled: ST.busy, onclick: () => { ST.confirming = false; paint(); }}, 'Cancel')));
    }
    async function save(makeRule) {
      if (ST.busy) return;
      ST.busy = true;
      paint();
      try {
        const res = await post('/api/transactions/category',
          {transaction_id: t.transaction_id, category: ST.sel, make_rule: makeRule});
        const extra = makeRule && res.changed > 1 ? ` ${plural(res.changed, 'transaction')} updated.` : '';
        toast([makeRule ? 'Rule saved: ' : 'Saved ', h('b', {}, t.merchant || 'transaction'), ' → ', h('b', {}, res.category), '.', extra],
          [], {timeout: 3500});
        ST.resolved += 1;
        ST.skipped.delete(t.transaction_id);   // resolved now: no longer a skipped row
        ST.rows.splice(ST.idx, 1);   // drop the resolved item; the next one slides into its place
        loadCounts();
        ST.busy = false;
        draw();
      } catch (e) {
        ST.busy = false;
        paint();
        toast(`Could not save: ${e.message}`, [], {error: true, timeout: 6000});
      }
    }
    paint();
    fill(page, card({title: 'Transaction review', icon: 'swap', rgb: RGB.acc, extra: head}, bar, body));
  }

  function skip() {
    if (ST.busy) return;
    const t = ST.rows[ST.idx];
    if (t) ST.skipped.add(t.transaction_id);   // skipped, not resolved: it stays unresolved and never counts as reviewed
    ST.idx += 1;
    draw();
  }

  function fact(label, valueEl) {
    return h('div', {}, h('dt', {}, label), h('dd', {}, valueEl));
  }

  function fmtDate(iso) {
    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
    return m ? `${MONTHS[Number(m[2]) - 1]} ${Number(m[3])}` : (iso || '');
  }

  RENDER.review = renderReview;
});
