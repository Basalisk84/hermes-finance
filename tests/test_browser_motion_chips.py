"""t_acbcd9f4 (motion A) and t_3d65332a (alert chips grid), in headless Chrome (tests/cdp.py). Synthetic demo data only,
on a throwaway loopback server.

Motion A: the bars refill and the charts grow (400ms) on a page switch and a month switch, not on a refresh in place,
and never under prefers-reduced-motion. Before it, the bars filled once per session and the charts never animated.
Needs attention: one prioritized card (it replaced the competing alert strip), ordered bank reconnect → overdue/due
bill → transaction review → budget warning, with the broken connection (the one with a Reconnect button) first and the
rest under an accessible View all expansion.

The demo DB is built for the fixed day below and the browser's clock is pinned to it (Chrome.pin_clock): the Overview takes
"this month" from `new Date()`, so unpinned the page asked for the month the gate itself ran in and, from 2026-11-01 on,
drew two chips from an October fixture (its 4 alerts include the two cap alerts that went missing).
"""

from __future__ import annotations

import time
from datetime import date

import pytest

import demo_data
from cdp import Chrome, Server, find_chrome
from hermes_finance import db

pytestmark = pytest.mark.skipif(find_chrome() is None, reason="Chrome is not installed")
TODAY = date(2026, 10, 20)
MONTH = TODAY.isoformat()[:7]

# what animated in the MS after ACT: mo-bar CSS animations started, and whether any Chart.js chart was animating
SAMPLE = """(async () => {
  let bars = 0, charts = false;
  const on = (e) => { if (e.animationName === 'mo-bar') bars++; };
  document.addEventListener('animationstart', on, true);
  ACT
  const end = performance.now() + MS;
  await new Promise((res) => { const tick = () => {
    const C = window.Chart;
    if (C && Object.values(C.instances || {}).some((c) => C.animator.running(c))) charts = true;
    if (performance.now() < end) requestAnimationFrame(tick); else res(); }; tick(); });
  document.removeEventListener('animationstart', on, true);
  return {bars, charts, hash: location.hash};
})()"""
PREV = "document.getElementById('mprev').click();"
RERENDER = "dispatchEvent(new HashChangeEvent('hashchange'));"


def go(page: str) -> str:
    return f"location.hash = '#/{page}';"


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    path = tmp_path_factory.mktemp("motionchips") / "finance.db"
    conn = db.connect(path)
    demo_data.build(conn, today=TODAY)
    demo_data.extras(conn, today=TODAY)
    conn.close()
    srv = Server(path, today=TODAY)
    yield srv
    srv.close()


@pytest.fixture(scope="module")
def browser():
    with Chrome() as b:
        b.pin_clock(TODAY)   # the page's "today" is the fixture's, whatever day this gate runs on
        yield b


def load(b, site, width=1440, reduced=False, page="overview"):
    b.call("Emulation.setEmulatedMedia",
           features=[{"name": "prefers-reduced-motion", "value": "reduce" if reduced else "no-preference"}])
    b.size(width, 900 if width > 800 else 844)
    b.goto(f"{site.url}/?t={time.time():.3f}#/{page}")
    b.settled()
    b.js("new Promise((r) => setTimeout(r, 700))")   # the first load's bars, charts and fade are over


def sample(b, act, ms=700):
    return b.js(SAMPLE.replace("ACT", act).replace("MS", str(ms)))


def test_returning_to_a_page_refills_its_bars_and_grows_its_charts(browser, site):
    load(browser, site, page="budgets")
    r = sample(browser, go("overview"))
    assert r["bars"] > 0, r          # was 0: bars filled once per session (body.bars-done stayed on)
    assert r["charts"], r            # was never: Chart.defaults.animation = false for every chart
    assert browser.problems() == []


def test_a_month_switch_refills_the_bars_and_grows_the_charts(browser, site):
    load(browser, site)
    r = sample(browser, PREV)
    assert "month=" in r["hash"]
    assert r["bars"] > 0 and r["charts"], r


def test_a_refresh_in_place_draws_still(browser, site):
    load(browser, site)
    r = sample(browser, RERENDER)
    assert r == {"bars": 0, "charts": False, "hash": r["hash"]}


def test_a_toggle_redrawn_in_place_draws_still(browser, site):
    # the gate's doughnut and drill tests read slice geometry right after a toggle: a regrowing chart broke them
    load(browser, site, page="investments")
    r = sample(browser, "document.querySelector('#page .seg button[data-k=type]').click();")
    assert r["charts"] is False, r
    assert browser.problems() == []


def test_reduced_motion_never_animates_bars_or_charts(browser, site):
    load(browser, site, reduced=True, page="budgets")
    for act in (go("overview"), PREV):
        r = sample(browser, act)
        assert r["bars"] == 0 and not r["charts"], (act, r)
    browser.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "no-preference"}])


# The one prioritized "Needs attention" card (it replaced the competing alert strip). Expand "View all" so every item
# is measured, and report whether the first item is the broken connection (the one that needs you, with a Reconnect
# button).
ATTN = """(() => {
  const card = document.querySelector('#page .attention');
  if (!card) return null;
  const details = card.querySelector('details.attn-more');
  if (details) details.open = true;
  const cr = card.getBoundingClientRect();
  const items = [...card.querySelectorAll('.attn')].map((c) => { const r = c.getBoundingClientRect();
    return {cls: c.className, top: Math.round(r.top), left: r.left, right: r.right, w: r.width,
            clipped: c.scrollWidth > c.clientWidth + 1, reconnect: !!c.querySelector('button.rc')}; });
  return {left: cr.left, right: cr.right, hasDetails: !!details, items}; })()"""


def test_needs_attention_is_one_prioritized_card_reconnect_first(browser, site):
    load(browser, site)
    s = browser.js(ATTN)
    assert s is not None, "the Overview shows the Needs attention card"
    items = s["items"]
    assert len(items) >= 2, items                                # the fixture has a broken bank plus budget warnings
    assert items[0]["reconnect"], items[0]                       # the broken connection comes first
    assert s["hasDetails"], "more than one item, so there is a View all expansion"
    assert len({c["top"] for c in items}) == len(items)          # one prioritized column, not a ragged grid
    for c in items:                                              # within the card, nothing clipped
        assert not c["clipped"], c
        assert c["left"] >= s["left"] - 0.5 and c["right"] <= s["right"] + 0.5, (c, s)


def test_needs_attention_fits_the_phone_width(browser, site):
    load(browser, site, width=390)
    s = browser.js(ATTN)
    assert s is not None
    for c in s["items"]:
        assert not c["clipped"], c
        assert c["left"] >= -0.5 and c["right"] <= 390 + 0.5, c   # no horizontal overflow at 390px
