"""The household command-center UI (task 237af77f): navigation grouping, the Overview command center and the single
prioritized Needs attention card, the focused transaction-review workflow, and contextual "Ask about this".

These are source-structure and server-behaviour checks that run without a browser (the layout/behaviour themselves are
exercised by the browser suite). They pin the invariants that must not regress: the ordered attention priorities, the
three primary figures and their one-source numbers, the rule-confirmation in the review flow, and that Ask only
prefills (it never sends or bypasses approval)."""

from __future__ import annotations

import re
from pathlib import Path

from hermes_finance import web

STATIC = Path(web.__file__).parent / "static"
APP = (STATIC / "app.js").read_text()
REVIEW = (STATIC / "review.js").read_text()
CHAT = (STATIC / "chat.js").read_text()
CSS = (STATIC / "app.css").read_text()


def fn(src: str, name: str) -> str:
    m = re.search(rf"\n  (?:async )?function {name}\(.*?\n  }}\n", src, re.S)
    assert m, name
    return m.group(0)


# --- navigation grouping ---------------------------------------------------------------------------------------------
def test_sidebar_is_grouped_daily_then_worth():
    assert web.NAV_GROUPS == (("Daily", ("overview", "transactions", "budgets", "recurring")),
                              ("Worth", ("accounts", "investments", "debt", "goals")))
    # PAGES order matches the groups (Daily run, then Worth run)
    assert [p[0] for p in web.PAGES] == ["overview", "transactions", "budgets", "recurring",
                                         "accounts", "investments", "debt", "goals"]


def test_phone_tabs_end_with_more():
    assert web.TABS == ("overview", "transactions", "budgets", "accounts", "more")
    assert web.MORE_TAB[0] == "more"
    # the drawer no longer reorders by CSS (DOM order is the group order now)
    assert "#nav a[data-pg=overview] { order:" not in CSS
    # the More page is a real route that lists every group and Settings
    assert "PAGES.more = 'More'" in APP or "more: 'More'" in APP
    assert "more: renderMore" in APP
    assert "['Daily'," in APP and "['Worth'," in APP            # MORE_GROUPS mirrors the command-center groups
    more = fn(APP, "renderMore")
    assert "has(RENDER, 'settings')" in more and "'Settings'" in more


# --- Overview command center + Needs attention -----------------------------------------------------------------------
def test_overview_renders_command_center_then_attention_then_grid():
    ov = fn(APP, "renderOverview")
    assert "fill(page, commandCenter(d), attentionCard(d), grid)" in ov
    assert "alertsStrip" not in APP, "the competing alert strip is replaced by one prioritized card"


def test_command_center_shows_the_three_primary_figures_from_one_source():
    cc = fn(APP, "commandCenter")
    assert "'Spent'" in cc and "s.mtd" in cc                       # Spent: summary.mtd
    assert "'Left to spend'" in cc and "b.left" in cc             # Left to spend: budgets.left
    assert "'Bills due'" in cc and "d.pace.bills_due_total" in cc  # Bills due: pace.bills_due_total
    assert "attentionItems(d).length" in cc                        # the Needs attention count


def test_attention_items_are_ordered_reconnect_bill_review_budget():
    items = fn(APP, "attentionItems")
    # the four priorities appear in the required order in the source
    order = [m.group(1) for m in re.finditer(r"pri: (\d)", items)]
    assert order[0] == "1", "bank reconnect first"
    assert "a.kind === 'connection'" in items.split("pri: 2")[0], "priority 1 is the broken connection"
    assert "d.review > 0" in items, "transaction review is an attention item"
    assert "a.kind !== 'cap'" in items or "a.kind === 'cap'" in items, "budget warnings are an attention item"
    assert "items.sort((a, b) => a.pri - b.pri)" in items
    # healthy stays quiet, and there is an accessible View all expansion
    card = fn(APP, "attentionCard")
    assert "attention ok" in card and "Nothing needs attention" in card
    assert "details" in card and "summary" in card and "View all" in card


def test_attention_and_command_center_have_styles():
    assert ".cc {" in CSS and ".cc-figs {" in CSS
    assert ".attention {" in CSS and ".attn {" in CSS
    assert ".attn-more > summary" in CSS


# --- transaction-review workflow -------------------------------------------------------------------------------------
def test_review_workflow_is_registered_and_uses_the_category_endpoint():
    assert "review" in web.FEATURE_FILES
    assert "PAGES.review = 'Review'" in REVIEW
    assert "RENDER.review = renderReview" in REVIEW
    assert "/api/transactions'" in REVIEW and "kind: 'review'" in REVIEW      # reads the review set
    assert "/api/transactions/category'" in REVIEW                            # writes via the one endpoint


def test_review_shows_the_required_fields_and_advances():
    draw = fn(REVIEW, "draw")
    for token in ("t.merchant", "signed(t.amount)", "account_name", "t.ai_note", "catPick", "to review"):
        assert token in draw, token
    # the two save buttons, and saving advances (splice the resolved row, redraw)
    assert "Just this one" in draw and "Always categorize this merchant" in draw
    assert "ST.rows.splice(ST.idx, 1)" in draw and "draw()" in draw


def test_review_requires_explicit_confirmation_for_a_rule():
    draw = fn(REVIEW, "draw")
    # the "Always…" button does not write; it opens a confirmation, and only "Create rule" posts make_rule: true
    assert "ST.confirming = true" in draw
    assert "make_rule: makeRule" in draw
    # "Just this one" posts with make_rule false, the confirm path with true
    assert "save(false)" in draw and "save(true)" in draw
    assert "Create rule" in draw


# --- contextual "Ask about this" -------------------------------------------------------------------------------------
def test_ask_prefill_opens_without_sending_and_respects_off():
    # chat.js exposes prefill; it fills the composer but never calls the send path on its own
    assert "prefill," in CHAT and "async function prefill(text)" in CHAT
    pre = re.search(r"async function prefill\(text\).*?\n  }\n", CHAT, re.S).group(0)
    assert "input.value = q" in pre
    assert "if (!enabled()) return false" in pre     # no-op when AI is off
    assert "post(" not in pre and "ask(" not in pre  # it does not send or write


def test_ask_about_button_is_gated_and_contextual():
    helper = re.search(r"function askAboutBtn\(.*?\n  }\n", APP, re.S).group(0)
    assert "if (!askEnabled()) return null" in helper          # only when the Ask panel is on
    assert "HF.chat.prefill" in APP                            # it prefills, never sends
    # wired into all four entity contexts, each naming the entity and the selected month (askMonth)
    assert "budget in ${askMonth()}" in APP                    # budget (capEditor)
    assert "account and how its value" in APP                  # account (asset detail)
    assert "charge of" in REVIEW and "askMonth()" in REVIEW    # transaction (review card)
    bills = (STATIC / "bills.js").read_text()
    assert "recurring bill" in bills and "askMonth()" in bills  # recurring bill (bill sheet)
