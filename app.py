"""
JND Med-Rates  -  Lab test price transparency for Junagadh.

Stack     : Streamlit + Supabase (supabase-py)
Deploy    : Streamlit Community Cloud
Security  : * Visitors use the read-only `anon` key (protected by RLS).
            * Admin writes use the `service_role` key, which is only ever
              instantiated AFTER the admin password has been verified.
            * Every database-provided string is HTML-escaped before it is
              injected into the page (no stored-XSS through lab names etc.).
"""

from __future__ import annotations

import hmac
import html
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import streamlit as st
from supabase import Client, create_client

# ---------------------------------------------------------------------------
# Page config - must be the first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="JND Med-Rates | Compare lab test prices in Junagadh",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="collapsed",
)

logger = logging.getLogger("jnd_med_rates")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TABLE = "lab_prices"
DEFAULT_WA_NUMBER = "919999999999"  # placeholder; override in secrets [app]
TOP_TESTS = ["CBC", "Lipid Profile", "Thyroid Profile", "HbA1c", "Urine Routine"]
STALE_AFTER_DAYS = 45               # show a "confirm price" hint after this
IST = timezone(timedelta(hours=5, minutes=30))
NEW_LAB = "+ Add a new lab"
OTHER_TEST = "Other (type the name)"
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 300


# ===========================================================================
# 1. Styling
# ===========================================================================
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700;12..96,800&family=DM+Sans:wght@400;500;600&display=swap');

:root{
  --ink:#10282B; --deep:#0C3B3E; --teal:#0B6E6E; --paper:#F6F9F8;
  --line:#D7E3E0; --mist:#E9F1EF; --muted:#52696C;
  --wa:#167A3E; --wa-hover:#115F30;
  --amber-bg:#FFF1CC; --amber-ink:#6B4A00;
  --display:'Bricolage Grotesque','DM Sans',system-ui,sans-serif;
}

.stApp{ background:var(--paper); font-family:'DM Sans',system-ui,sans-serif; color:var(--ink); }
.block-container{ max-width:1120px; padding-top:1.6rem; padding-bottom:3rem; }
#MainMenu, footer, .stDeployButton{ visibility:hidden; display:none; }
header[data-testid="stHeader"]{ background:transparent; }

/* ---------- Hero ---------- */
.hero{
  background:var(--deep); color:#fff; border-radius:24px;
  padding:28px 40px 44px; margin-bottom:28px;
}
.hero-nav{ display:flex; justify-content:space-between; align-items:center; margin-bottom:40px; gap:12px; flex-wrap:wrap; }
.wordmark{ font-family:var(--display); font-weight:800; font-size:1.25rem; letter-spacing:-.01em; }
.wordmark span{ color:#7FD6C8; }
.pill{ border:1px solid rgba(255,255,255,.35); border-radius:999px; padding:5px 14px; font-size:.85rem; color:#D8EEEA; }
.hero-grid{ display:grid; grid-template-columns:1.35fr 1fr; gap:40px; align-items:center; }
.hero h1{
  font-family:var(--display); font-weight:800; color:#fff; margin:0 0 16px;
  font-size:clamp(2.1rem,4.6vw,3.4rem); line-height:1.04; letter-spacing:-.025em; padding:0;
}
.hero p.sub{ color:#C5DEDA; font-size:1.08rem; line-height:1.6; max-width:34rem; margin:0; }

/* receipt-style "how it works" slip */
.slip{
  background:#fff; color:var(--ink); border-radius:6px; padding:22px 24px;
  transform:rotate(-1.4deg); box-shadow:0 18px 40px -18px rgba(0,0,0,.55);
  border-bottom:10px dashed var(--mist);
}
.slip h3{ font-family:var(--display); font-size:1.05rem; margin:0 0 14px; padding:0; color:var(--ink); }
.slip ol{ margin:0; padding-left:1.2rem; display:grid; gap:10px; font-size:.97rem; line-height:1.4; }
.slip li::marker{ font-family:var(--display); font-weight:800; color:var(--teal); }
.slip .rule{ border-top:1px dashed var(--line); margin:16px 0 10px; }
.slip .foot{ font-size:.82rem; color:var(--muted); }

@media (max-width:820px){
  .hero{ padding:22px 22px 32px; }
  .hero-nav{ margin-bottom:26px; }
  .hero-grid{ grid-template-columns:1fr; gap:28px; }
  .slip{ transform:none; }
}

/* ---------- Controls ---------- */
div[data-baseweb="select"] > div{
  border-radius:12px; border:1px solid var(--line); min-height:52px; background:#fff;
}
div[data-baseweb="select"] > div:focus-within{ border-color:var(--teal); box-shadow:0 0 0 3px rgba(11,110,110,.18); }
.stApp label p{ font-weight:600; color:var(--ink); }

/* ---------- Summary strip ---------- */
.summary{
  background:#fff; border:1px solid var(--line); border-left:5px solid var(--teal);
  border-radius:12px; padding:14px 18px; margin:18px 0 22px;
  font-size:1.02rem; line-height:1.5;
}
.summary b{ font-family:var(--display); }

/* ---------- Result cards ---------- */
.card{
  background:#fff; border:1px solid var(--line); border-radius:16px;
  padding:20px 22px; margin-bottom:18px; display:flex; flex-direction:column; gap:14px;
}
.card.best{ border:2px solid var(--teal); box-shadow:0 14px 30px -18px rgba(11,110,110,.55); }
.card-top{ display:flex; justify-content:space-between; align-items:flex-start; gap:12px; }
.lab{ font-family:var(--display); font-weight:700; font-size:1.18rem; line-height:1.25; color:var(--ink); }
.testname{ font-size:.9rem; color:var(--muted); margin-top:2px; }
.badge{
  background:var(--teal); color:#fff; font-weight:600; font-size:.8rem;
  padding:4px 10px; border-radius:999px; white-space:nowrap;
}
.price-row{ display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }
.price{ font-family:var(--display); font-weight:800; font-size:2.6rem; line-height:1; letter-spacing:-.02em; color:var(--ink); }
.price .rs{ font-size:1.5rem; font-weight:700; margin-right:2px; color:var(--teal); }
.delta{ font-size:.9rem; color:var(--muted); }
.track{ height:6px; background:var(--mist); border-radius:99px; overflow:hidden; }
.fill{ height:100%; background:var(--teal); border-radius:99px; }
.card:not(.best) .fill{ background:#7FA9A6; }
.meta{ display:grid; gap:7px; font-size:.92rem; color:var(--muted); line-height:1.45; }
.meta div{ display:flex; gap:8px; align-items:flex-start; }
.meta svg{ flex:0 0 auto; margin-top:2px; }
.chip-stale{ background:var(--amber-bg); color:var(--amber-ink); border-radius:6px; padding:1px 8px; font-size:.8rem; font-weight:600; }

.cta{
  display:flex; align-items:center; justify-content:center; gap:9px;
  background:var(--wa); color:#fff !important; text-decoration:none !important;
  font-weight:600; font-size:1rem; border-radius:12px; padding:13px 16px;
  transition:background .15s ease;
}
.cta:hover{ background:var(--wa-hover); }
.cta:focus-visible, .ghost:focus-visible{ outline:3px solid #F2B531; outline-offset:2px; }
.links{ display:flex; gap:10px; }
.ghost{
  flex:1; text-align:center; border:1px solid var(--line); border-radius:10px; padding:9px 10px;
  color:var(--teal) !important; text-decoration:none !important; font-weight:600; font-size:.92rem; background:#fff;
}
.ghost:hover{ background:var(--mist); }

/* ---------- Empty state / footer ---------- */
.empty{
  background:#fff; border:1px dashed var(--teal); border-radius:16px; padding:34px 26px; text-align:center;
}
.empty h3{ font-family:var(--display); margin:0 0 6px; padding:0; }
.empty p{ color:var(--muted); margin:0 0 16px; }
.empty .cta{ display:inline-flex; padding:12px 22px; }
.disclaimer{ color:var(--muted); font-size:.85rem; line-height:1.6; border-top:1px solid var(--line); padding-top:18px; margin-top:26px; }

@media (prefers-reduced-motion:reduce){ *{ transition:none !important; } }
</style>
"""

ICON_PIN = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#0B6E6E" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/>'
    '<circle cx="12" cy="10" r="2.5"/></svg>'
)
ICON_CLOCK = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#0B6E6E" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>'
)
ICON_WA = (
    '<svg width="19" height="19" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
    '<path d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5-1.3A10 10 0 1 0 12 2zm0 18a8 8 0 0 1-4.1-1.1l-.3-.2-3 .8.8-2.9-.2-.3A8 8 0 1 1 12 20z"/>'
    '<path d="M9 7.8c.2-.4.4-.4.6-.4h.5c.2 0 .4.1.5.4l.8 1.8c.1.2 0 .4-.1.5l-.5.7c-.1.1-.1.3 0 .4.5.9 1.3 1.6 2.3 2 .2.1.3 0 .4-.1l.6-.7c.1-.2.3-.2.5-.1l1.7.8c.2.1.3.2.3.4 0 .6-.4 1.4-1.1 1.7-.6.3-1.3.3-2.9-.4-1.9-.8-3.3-2.5-3.8-3.4-.5-.9-.7-1.9.2-2.8z"/></svg>'
)


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def compact(markup: str) -> str:
    """Strip indentation/blank lines so Markdown never treats HTML as a code block."""
    return "".join(line.strip() for line in markup.splitlines())


# ===========================================================================
# 2. Small helpers (formatting, sanitising)
# ===========================================================================
def esc(value: object) -> str:
    """HTML-escape any value coming from the database or user input."""
    return html.escape(str(value or ""), quote=True)


def secret(section: str, key: str, default: str | None = None) -> str | None:
    """Read a value from st.secrets without crashing if it is missing."""
    try:
        return st.secrets[section][key]
    except (KeyError, FileNotFoundError, AttributeError):
        return default


def format_inr(value: float) -> str:
    """Format a number using Indian digit grouping: 1234567 -> 12,34,567."""
    text = f"{value:.2f}".rstrip("0").rstrip(".") if value % 1 else str(int(value))
    whole, _, decimals = text.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = re.sub(r"(\d)(?=(\d{2})+$)", r"\1,", head)
        whole = f"{head},{tail}"
    return whole + (f".{decimals}" if decimals else "")


def digits_only(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def is_http_url(value: str | None) -> bool:
    return bool(value) and bool(re.match(r"^https?://[^\s]+$", value.strip(), re.IGNORECASE))


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(IST)
    except ValueError:
        return None


def whatsapp_link(number: str, test: str, lab: str, price: float) -> str:
    """Build a wa.me deep link with a pre-filled booking message."""
    message = f"I want to book {test} at {lab} (listed at ₹{format_inr(price)} on JND Med-Rates)."
    return f"https://wa.me/{digits_only(number)}?text={quote(message)}"


def tel_link(number: str | None) -> str | None:
    digits = digits_only(number)
    if len(digits) == 10:
        digits = "91" + digits
    return f"tel:+{digits}" if len(digits) >= 11 else None


# ===========================================================================
# 3. Database layer (Supabase)
# ===========================================================================
@st.cache_resource(show_spinner=False)
def get_client(role: str) -> Client:
    """
    role = "public" -> anon key (read-only via RLS)
    role = "admin"  -> service_role key (write access). Only call after login.
    """
    key_name = "anon_key" if role == "public" else "service_role_key"
    return create_client(st.secrets["supabase"]["url"], st.secrets["supabase"][key_name])


def normalize_row(raw: dict) -> dict | None:
    """Coerce a DB row into safe types; drop rows with unusable prices."""
    try:
        price = float(raw["price_inr"])
    except (KeyError, TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return {
        "id": raw.get("id"),
        "lab_name": (raw.get("lab_name") or "").strip(),
        "test_name": (raw.get("test_name") or "").strip(),
        "price": price,
        "address": (raw.get("address") or "").strip(),
        "map_link": (raw.get("map_link") or "").strip(),
        "contact_number": (raw.get("contact_number") or "").strip(),
        "last_updated": parse_timestamp(raw.get("last_updated")),
    }


@st.cache_data(ttl=60, show_spinner=False)
def fetch_all_prices() -> list[dict]:
    """One cached query for the whole (city-sized) catalogue; filtered client-side."""
    response = (
        get_client("public")
        .table(TABLE)
        .select("*")
        .order("price_inr")
        .range(0, 4999)
        .execute()
    )
    rows = [normalize_row(r) for r in (response.data or [])]
    return [r for r in rows if r and r["lab_name"] and r["test_name"]]


def load_prices() -> tuple[list[dict], str | None]:
    """Never raises: returns (rows, error_message)."""
    try:
        return fetch_all_prices(), None
    except KeyError:
        return [], "The app is missing its Supabase credentials. Add them under [supabase] in secrets."
    except Exception as exc:  # network, auth, RLS, malformed response...
        logger.exception("Failed to load prices: %s", exc)
        return [], "We couldn't load prices right now. Please try again in a minute."


def ordered_tests(rows: list[dict]) -> list[str]:
    """Top tests first (always listed), then any other tests found in the DB."""
    extra = sorted({r["test_name"] for r in rows} - set(TOP_TESTS), key=str.lower)
    return TOP_TESTS + extra


def save_price(payload: dict) -> tuple[bool, str]:
    try:
        get_client("admin").table(TABLE).upsert(payload, on_conflict="lab_name,test_name").execute()
        fetch_all_prices.clear()
        return True, f"Saved: {payload['test_name']} at {payload['lab_name']} is now ₹{format_inr(payload['price_inr'])}."
    except Exception as exc:
        logger.exception("Save failed: %s", exc)
        return False, f"Couldn't save this price: {exc}"


def delete_price(row_id: int) -> tuple[bool, str]:
    try:
        get_client("admin").table(TABLE).delete().eq("id", row_id).execute()
        fetch_all_prices.clear()
        return True, "Entry removed."
    except Exception as exc:
        logger.exception("Delete failed: %s", exc)
        return False, f"Couldn't remove this entry: {exc}"


# ===========================================================================
# 4. Public UI components
# ===========================================================================
def render_hero() -> None:
    st.markdown(
        compact(
            """
            <section class="hero">
              <div class="hero-nav">
                <div class="wordmark">JND <span>Med-Rates</span></div>
                <div class="pill">Junagadh, Gujarat</div>
              </div>
              <div class="hero-grid">
                <div>
                  <h1>Same test. Different price. Check before you book.</h1>
                  <p class="sub">Compare what Junagadh's labs charge for everyday blood and urine tests,
                  then book in one WhatsApp message. Built for families who'd rather not overpay.</p>
                </div>
                <aside class="slip" aria-label="How it works">
                  <h3>How it works</h3>
                  <ol>
                    <li>Pick the test your doctor asked for.</li>
                    <li>See every lab's price, cheapest first.</li>
                    <li>Tap Book via WhatsApp. We confirm your slot.</li>
                  </ol>
                  <div class="rule"></div>
                  <div class="foot">Prices are collected and updated by our team.</div>
                </aside>
              </div>
            </section>
            """
        ),
        unsafe_allow_html=True,
    )


def build_card(row: dict, *, is_best: bool, solo: bool, min_price: float, max_price: float, wa_number: str) -> str:
    price = row["price"]
    fill = max(8, round(price / max_price * 100)) if max_price else 100

    if solo:
        delta = "Only lab listed so far"
    elif is_best:
        delta = "Lowest listed price"
    else:
        delta = f"₹{format_inr(price - min_price)} more than the lowest"

    badge = '<span class="badge">Best price</span>' if is_best and not solo else ""

    updated = row["last_updated"]
    if updated:
        stale = (datetime.now(IST) - updated).days > STALE_AFTER_DAYS
        updated_html = f"Updated {updated.strftime('%d %b %Y')}"
        if stale:
            updated_html += ' <span class="chip-stale">Confirm price when booking</span>'
    else:
        updated_html = "Update date unavailable"

    links = []
    if is_http_url(row["map_link"]):
        links.append(f'<a class="ghost" href="{esc(row["map_link"])}" target="_blank" rel="noopener noreferrer">Open map</a>')
    tel = tel_link(row["contact_number"])
    if tel:
        links.append(f'<a class="ghost" href="{esc(tel)}">Call lab</a>')
    links_html = f'<div class="links">{"".join(links)}</div>' if links else ""

    address_html = f"<div>{ICON_PIN}<span>{esc(row['address'])}</span></div>" if row["address"] else ""

    return compact(
        f"""
        <article class="card{' best' if is_best and not solo else ''}">
          <div class="card-top">
            <div>
              <div class="lab">{esc(row['lab_name'])}</div>
              <div class="testname">{esc(row['test_name'])}</div>
            </div>
            {badge}
          </div>
          <div>
            <div class="price-row">
              <div class="price"><span class="rs">₹</span>{format_inr(price)}</div>
              <div class="delta">{esc(delta)}</div>
            </div>
            <div class="track" style="margin-top:12px" aria-hidden="true"><div class="fill" style="width:{fill}%"></div></div>
          </div>
          <div class="meta">
            {address_html}
            <div>{ICON_CLOCK}<span>{updated_html}</span></div>
          </div>
          <a class="cta" href="{esc(whatsapp_link(wa_number, row['test_name'], row['lab_name'], price))}"
             target="_blank" rel="noopener noreferrer">{ICON_WA}Book via WhatsApp</a>
          {links_html}
        </article>
        """
    )


def render_summary(test: str, results: list[dict]) -> None:
    prices = [r["price"] for r in results]
    low, high = min(prices), max(prices)
    if len(results) == 1:
        text = f"<b>{esc(test)}</b> is listed at 1 lab for <b>₹{format_inr(low)}</b>. More labs are being added."
    elif high == low:
        text = f"<b>{len(results)} labs</b> list {esc(test)} at the same price, <b>₹{format_inr(low)}</b>."
    else:
        text = (
            f"<b>{len(results)} labs</b> list {esc(test)} between <b>₹{format_inr(low)}</b> and "
            f"<b>₹{format_inr(high)}</b>. Choosing the lowest saves you <b>₹{format_inr(high - low)}</b>."
        )
    st.markdown(f'<div class="summary">{text}</div>', unsafe_allow_html=True)


def render_empty_state(test: str, wa_number: str) -> None:
    message = quote(f"Hi, I'm looking for the price of {test} in Junagadh.")
    st.markdown(
        compact(
            f"""
            <div class="empty">
              <h3>No prices for {esc(test)} yet</h3>
              <p>We're adding labs every week. Message us and we'll find a price for you.</p>
              <a class="cta" href="https://wa.me/{digits_only(wa_number)}?text={message}"
                 target="_blank" rel="noopener noreferrer">{ICON_WA}Ask us on WhatsApp</a>
            </div>
            """
        ),
        unsafe_allow_html=True,
    )


def render_results(rows: list[dict], wa_number: str) -> None:
    tests = ordered_tests(rows)

    col_test, col_sort = st.columns([3, 1.4], gap="large", vertical_alignment="bottom")
    with col_test:
        test = st.selectbox("Which test do you need?", tests, index=0, key="selected_test")
    with col_sort:
        cheapest_first = st.toggle("Cheapest first", value=True, help="Turn off to see the most recently updated prices first.")

    results = [r for r in rows if r["test_name"] == test]
    if not results:
        render_empty_state(test, wa_number)
        return

    if cheapest_first:
        results.sort(key=lambda r: (r["price"], r["lab_name"].lower()))
    else:
        results.sort(key=lambda r: r["last_updated"] or datetime(1970, 1, 1, tzinfo=IST), reverse=True)

    prices = [r["price"] for r in results]
    min_price, max_price = min(prices), max(prices)
    solo = len(results) == 1

    render_summary(test, results)

    # Two-column responsive grid (Streamlit stacks columns on mobile).
    for start in range(0, len(results), 2):
        cols = st.columns(2, gap="medium")
        for col, row in zip(cols, results[start:start + 2]):
            with col:
                st.markdown(
                    build_card(
                        row,
                        is_best=row["price"] == min_price,
                        solo=solo,
                        min_price=min_price,
                        max_price=max_price,
                        wa_number=wa_number,
                    ),
                    unsafe_allow_html=True,
                )


def render_footer() -> None:
    st.markdown(
        """
        <div class="disclaimer">
          Prices are indicative and collected from labs by the JND Med-Rates team. Final charges, home-collection fees
          and report timings are set by the lab, so please confirm when you book. This site does not give medical advice;
          follow your doctor's prescription for which tests you need.
        </div>
        """,
        unsafe_allow_html=True,
    )


# ===========================================================================
# 5. Admin panel (sidebar)
# ===========================================================================
def verify_password(entered: str) -> bool:
    expected = secret("admin", "password")
    if not expected or not entered:
        return False
    return hmac.compare_digest(entered.encode("utf-8"), str(expected).encode("utf-8"))


def render_login() -> None:
    now = time.time()
    locked_until = st.session_state.get("locked_until", 0)
    if now < locked_until:
        st.warning(f"Too many attempts. Try again in {int(locked_until - now) // 60 + 1} min.")
        return

    with st.form("admin_login", clear_on_submit=True):
        entered = st.text_input("Password", type="password", placeholder="Admin password", label_visibility="collapsed")
        submitted = st.form_submit_button("Unlock", use_container_width=True)

    if submitted:
        if verify_password(entered):
            st.session_state["is_admin"] = True
            st.session_state["failed_logins"] = 0
            st.rerun()
        else:
            fails = st.session_state.get("failed_logins", 0) + 1
            st.session_state["failed_logins"] = fails
            time.sleep(1)  # slows down guessing
            if fails >= MAX_LOGIN_ATTEMPTS:
                st.session_state["locked_until"] = time.time() + LOCKOUT_SECONDS
                st.session_state["failed_logins"] = 0
                st.error("Too many wrong attempts. Locked for 5 minutes.")
            else:
                st.error("Incorrect password.")


def render_admin_panel(rows: list[dict]) -> None:
    flash = st.session_state.pop("admin_flash", None)
    if flash:
        (st.success if flash[0] else st.error)(flash[1])

    st.caption("Signed in as admin")

    labs = sorted({r["lab_name"] for r in rows}, key=str.lower)
    lab_choice = st.selectbox("Lab", [NEW_LAB] + labs, key="adm_lab")
    lab_name = (
        st.text_input("New lab name", key="adm_new_lab").strip() if lab_choice == NEW_LAB else lab_choice
    )

    test_choice = st.selectbox("Test", ordered_tests(rows) + [OTHER_TEST], key="adm_test")

    existing = next((r for r in rows if r["lab_name"] == lab_name and r["test_name"] == test_choice), None)
    lab_info = next((r for r in rows if r["lab_name"] == lab_name), None) or {}
    defaults = existing or lab_info

    if existing:
        st.info(f"Updating the current price of ₹{format_inr(existing['price'])}.")

    form_key = f"{lab_name}|{test_choice}"
    with st.form("admin_price_form"):
        custom_test = ""
        if test_choice == OTHER_TEST:
            custom_test = st.text_input("Test name", key=f"adm_custom_{lab_name}")
        price = st.number_input(
            "Price (₹)", min_value=0.0, max_value=100000.0, step=10.0,
            value=float(existing["price"]) if existing else 0.0, key=f"adm_price_{form_key}",
        )
        address = st.text_area("Address", value=defaults.get("address", ""), key=f"adm_addr_{lab_name}", height=80)
        map_link = st.text_input("Google Maps link", value=defaults.get("map_link", ""), key=f"adm_map_{lab_name}")
        contact = st.text_input("Lab contact number", value=defaults.get("contact_number", ""), key=f"adm_tel_{lab_name}")
        submitted = st.form_submit_button("Save price", type="primary", use_container_width=True)

    if submitted:
        final_test = re.sub(r"\s+", " ", (custom_test if test_choice == OTHER_TEST else test_choice)).strip()
        lab_clean = re.sub(r"\s+", " ", lab_name).strip()
        phone = digits_only(contact)

        problems = []
        if not lab_clean:
            problems.append("Enter a lab name.")
        if not final_test:
            problems.append("Enter a test name.")
        if price <= 0:
            problems.append("Price must be more than ₹0.")
        if not address.strip():
            problems.append("Enter the lab's address.")
        if map_link.strip() and not is_http_url(map_link):
            problems.append("The map link must start with http:// or https://")
        if contact.strip() and not 10 <= len(phone) <= 13:
            problems.append("Contact number should have 10 to 13 digits.")

        if problems:
            for p in problems:
                st.error(p)
        else:
            ok, msg = save_price(
                {
                    "lab_name": lab_clean,
                    "test_name": final_test,
                    "price_inr": round(price, 2),
                    "address": address.strip(),
                    "map_link": map_link.strip() or None,
                    "contact_number": phone or None,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }
            )
            st.session_state["admin_flash"] = (ok, msg)
            st.rerun()

    # Streamlit doesn't allow nested expanders, so this is a simple toggle.
    if st.toggle("Show remove tool", key="adm_show_remove"):
        if not rows:
            st.caption("Nothing to remove yet.")
        else:
            options = {f"{r['lab_name']} | {r['test_name']} | ₹{format_inr(r['price'])}": r["id"] for r in rows}
            label = st.selectbox("Entry", list(options), key="adm_delete_pick")
            confirm = st.checkbox("Yes, remove this entry permanently", key="adm_delete_confirm")
            if st.button("Remove entry", disabled=not confirm, use_container_width=True):
                ok, msg = delete_price(options[label])
                st.session_state["admin_flash"] = (ok, msg)
                st.rerun()

    if st.button("Sign out", use_container_width=True):
        for key in [k for k in st.session_state if k.startswith("adm_")] + ["is_admin"]:
            st.session_state.pop(key, None)
        st.rerun()


def render_sidebar(rows: list[dict]) -> None:
    with st.sidebar:
        st.markdown("**JND Med-Rates**")
        with st.expander("Staff access", expanded=bool(st.session_state.get("is_admin"))):
            if not secret("admin", "password") or not secret("supabase", "service_role_key"):
                st.caption("Admin access isn't configured.")
            elif st.session_state.get("is_admin"):
                render_admin_panel(rows)
            else:
                render_login()


# ===========================================================================
# 6. App entry point
# ===========================================================================
def main() -> None:
    inject_css()
    wa_number = digits_only(secret("app", "whatsapp_number", DEFAULT_WA_NUMBER)) or DEFAULT_WA_NUMBER

    rows, error = load_prices()
    render_sidebar(rows)
    render_hero()

    if error:
        st.error(error)
    else:
        render_results(rows, wa_number)

    render_footer()


main()
