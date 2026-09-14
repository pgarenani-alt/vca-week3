"""
VCA Week 3 — BLOCK HUNT
Villanova Blockchain Society · Villanova Crypto Academy · Fall 2026
Session: Monday 14 September 2026 — Bitcoin and Blockchain Mechanics

Student view:   /
Presenter view: /?p=present
"""

import io
import json
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

# ----------------------------------------------------------------------------
# CONSTANTS
# ----------------------------------------------------------------------------

NAVY = "#0B1E3D"
GOLD = "#C6A15B"
TEAL = "#1B8F8A"
INK = "#F2F4F8"

HALVING_BLOCK = 1_050_000          # next halving height
BLOCKS_PER_DAY = 144               # 6 blocks/hr * 24
CURRENT_SUBSIDY = 3.125            # BTC per block, post-2024 halving
ETH_TRANSFER_GAS = 21_000          # gas units for a plain ETH transfer
SOL_BASE_FEE_LAMPORTS = 5_000      # per signature
LAMPORTS_PER_SOL = 1_000_000_000

DB_PATH = os.environ.get("VCA_DB_PATH", "vca_w3.db")
_LOCK = threading.Lock()

# Fallback values — used only if the presenter never locks and the live fetch
# fails. Sourced 14 Sep 2026 from bitcoinblockhalf.com / coinwarz.
FALLBACK = {
    "height": 967_011,
    "subsidy": 3.125,
    "total_fees_btc": 0.042,
    "tx_count": 3100,
    "circulating_btc": 20_084_409.0,
    "btc_price": 79_300.0,
    "eth_price": 2_516.0,
    "sol_price": 104.87,
    "eth_base_fee_gwei": 0.10,
    "locked_at": "",
    "locked": 0,
}


# ----------------------------------------------------------------------------
# STORE  (sqlite — one process on Streamlit Cloud / Railway, so all students
# share it; survives app reruns and restarts mid-session)
# ----------------------------------------------------------------------------

_CONN = None


def get_conn():
    """One connection per process, created exactly once even if 30 students
    hit the app in the same second. Double-checked locking, not st.cache_resource,
    so initialisation can never race."""
    global _CONN
    if _CONN is None:
        with _LOCK:
            if _CONN is None:
                conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=8000")
                conn.execute("""CREATE TABLE IF NOT EXISTS cfg (k TEXT PRIMARY KEY, v TEXT)""")
                conn.execute("""CREATE TABLE IF NOT EXISTS results (
                    email TEXT PRIMARY KEY,
                    name TEXT,
                    score INTEGER,
                    r1 INTEGER, r2 INTEGER, r3 INTEGER,
                    answers TEXT,
                    line TEXT,
                    submitted_at TEXT,
                    elapsed REAL
                )""")
                conn.commit()
                _CONN = conn
    return _CONN


def cfg_get(key, default=None):
    conn = get_conn()
    with _LOCK:
        row = conn.execute("SELECT v FROM cfg WHERE k=?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except Exception:
        return default


def cfg_set(key, value):
    conn = get_conn()
    with _LOCK:
        conn.execute(
            "INSERT INTO cfg (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, json.dumps(value)),
        )
        conn.commit()


def get_lock():
    """The single set of numbers every student is graded against."""
    locked = cfg_get("lock")
    if not locked:
        return dict(FALLBACK)
    merged = dict(FALLBACK)
    merged.update(locked)
    return merged


def save_result(rec):
    conn = get_conn()
    with _LOCK:
        conn.execute(
            """INSERT INTO results (email,name,score,r1,r2,r3,answers,line,submitted_at,elapsed)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(email) DO UPDATE SET
                 name=excluded.name, score=excluded.score, r1=excluded.r1,
                 r2=excluded.r2, r3=excluded.r3, answers=excluded.answers,
                 line=excluded.line, submitted_at=excluded.submitted_at,
                 elapsed=excluded.elapsed""",
            (rec["email"], rec["name"], rec["score"], rec["r1"], rec["r2"], rec["r3"],
             json.dumps(rec["answers"]), rec["line"], rec["submitted_at"], rec["elapsed"]),
        )
        get_conn().commit()


def all_results():
    conn = get_conn()
    with _LOCK:
        rows = conn.execute(
            "SELECT name,email,score,r1,r2,r3,line,submitted_at,elapsed "
            "FROM results ORDER BY score DESC, elapsed ASC"
        ).fetchall()
    return rows


def clear_results():
    conn = get_conn()
    with _LOCK:
        conn.execute("DELETE FROM results")
        conn.commit()


# ----------------------------------------------------------------------------
# LIVE DATA
# ----------------------------------------------------------------------------

def fetch_btc_block():
    """Latest confirmed block from mempool.space. Returns dict or raises."""
    r = requests.get("https://mempool.space/api/v1/blocks", timeout=12)
    r.raise_for_status()
    blocks = r.json()
    b = blocks[0]
    extras = b.get("extras") or {}
    total_fees_sats = extras.get("totalFees")
    reward_sats = extras.get("reward")
    if total_fees_sats is None and reward_sats is not None:
        total_fees_sats = reward_sats - int(CURRENT_SUBSIDY * 1e8)
    return {
        "height": int(b["height"]),
        "tx_count": int(b.get("tx_count", 0)),
        "total_fees_btc": round((total_fees_sats or 0) / 1e8, 6),
        "pool": (extras.get("pool") or {}).get("name", ""),
        "timestamp": int(b.get("timestamp", 0)),
    }


def fetch_supply_and_prices():
    out = {}
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin,ethereum,solana", "vs_currencies": "usd"},
            timeout=12,
        )
        r.raise_for_status()
        d = r.json()
        out["btc_price"] = float(d["bitcoin"]["usd"])
        out["eth_price"] = float(d["ethereum"]["usd"])
        out["sol_price"] = float(d["solana"]["usd"])
    except Exception:
        pass
    try:
        r = requests.get("https://blockchain.info/q/totalbc", timeout=12)
        r.raise_for_status()
        out["circulating_btc"] = float(r.text.strip()) / 1e8
    except Exception:
        pass
    return out


# ----------------------------------------------------------------------------
# ANSWER KEY — derived entirely from the locked numbers
# ----------------------------------------------------------------------------

def answer_key(L):
    height = L["height"]
    subsidy = L["subsidy"]
    fees = L["total_fees_btc"]
    circ = L["circulating_btc"]

    miner_rev = subsidy + fees
    fee_share_pct = (fees / miner_rev) * 100 if miner_rev else 0.0
    blocks_left = HALVING_BLOCK - height
    annual_btc = subsidy * BLOCKS_PER_DAY * 365
    annual_pct = (annual_btc / circ) * 100 if circ else 0.0
    days_left = blocks_left / BLOCKS_PER_DAY

    eth_fee_eth = (L["eth_base_fee_gwei"] * 1e-9) * ETH_TRANSFER_GAS
    eth_fee_usd = eth_fee_eth * L["eth_price"]
    sol_fee_usd = (SOL_BASE_FEE_LAMPORTS / LAMPORTS_PER_SOL) * L["sol_price"]

    return {
        "subsidy": subsidy,
        "fees": fees,
        "tx_count": L["tx_count"],
        "miner_rev": miner_rev,
        "fee_share_pct": fee_share_pct,
        "blocks_left": blocks_left,
        "annual_btc": annual_btc,
        "annual_pct": annual_pct,
        "days_left": days_left,
        "eth_fee_usd": eth_fee_usd,
        "sol_fee_usd": sol_fee_usd,
        "eth_fee_eth": eth_fee_eth,
    }


# ----------------------------------------------------------------------------
# GRADING
# ----------------------------------------------------------------------------

def grade_numeric(given, target, points, rel_tol=None, abs_tol=None):
    """Full points inside the band, half points inside 2x the band, else 0."""
    if given is None:
        return 0, "no answer"
    try:
        g = float(given)
    except (TypeError, ValueError):
        return 0, "not a number"
    if abs_tol is not None:
        band = abs_tol
    else:
        band = abs(target) * (rel_tol if rel_tol is not None else 0.05)
    diff = abs(g - target)
    if diff <= band:
        return points, "correct"
    if diff <= band * 2:
        return int(points * 0.5), "close — half credit"
    return 0, "off"


def grade_choice(given, correct, points):
    if given is None:
        return 0, "no answer"
    return (points, "correct") if given == correct else (0, "off")


# ----------------------------------------------------------------------------
# QUESTION BANK  —  1000 points:  R1 400 · R2 350 · R3 250
# ----------------------------------------------------------------------------

MCQ_HASHRATE = [
    "Roughly doubles",
    "Roughly unchanged",
    "Falls by about half",
    "Depends on the fee market",
]
MCQ_BASEFEE = [
    "The validator who proposes the block",
    "It is burned — no one receives it",
    "The Ethereum Foundation treasury",
    "Split 50/50 between validator and burn",
]
MCQ_SOLFEE = ["About $0.001", "About $0.10", "About $1.00", "About $10.00"]


def score_submission(ans, L):
    K = answer_key(L)
    detail = []

    # ---- Round 1 · The Block (400) -----------------------------------------
    p, m = grade_numeric(ans.get("q11"), K["subsidy"], 80, abs_tol=0.001)
    detail.append(("R1", "Block subsidy (BTC)", ans.get("q11"), f"{K['subsidy']:.3f}", p, 80, m))
    p2, m2 = grade_numeric(ans.get("q12"), K["fees"], 100, rel_tol=0.12)
    detail.append(("R1", "Total fees in the block (BTC)", ans.get("q12"), f"{K['fees']:.4f}", p2, 100, m2))
    p3, m3 = grade_numeric(ans.get("q13"), K["tx_count"], 80, rel_tol=0.08)
    detail.append(("R1", "Transactions in the block", ans.get("q13"), f"{K['tx_count']:,}", p3, 80, m3))
    p4, m4 = grade_numeric(ans.get("q14"), K["fee_share_pct"], 140, rel_tol=0.18)
    detail.append(("R1", "Fees as % of miner revenue", ans.get("q14"), f"{K['fee_share_pct']:.2f}%", p4, 140, m4))
    r1 = p + p2 + p3 + p4

    # ---- Round 2 · The Schedule (350) --------------------------------------
    p, m = grade_numeric(ans.get("q21"), K["blocks_left"], 80, abs_tol=2)
    detail.append(("R2", "Blocks until the next halving", ans.get("q21"), f"{K['blocks_left']:,}", p, 80, m))
    p2, m2 = grade_numeric(ans.get("q22"), K["annual_btc"], 90, rel_tol=0.03)
    detail.append(("R2", "New BTC issued per year", ans.get("q22"), f"{K['annual_btc']:,.0f}", p2, 90, m2))
    p3, m3 = grade_numeric(ans.get("q23"), K["annual_pct"], 90, abs_tol=0.06)
    detail.append(("R2", "Annual issuance as % of circulating", ans.get("q23"), f"{K['annual_pct']:.2f}%", p3, 90, m3))
    p4, m4 = grade_choice(ans.get("q24"), MCQ_HASHRATE[1], 90)
    detail.append(("R2", "If hashrate doubles, issuance…", ans.get("q24"), MCQ_HASHRATE[1], p4, 90, m4))
    r2 = p + p2 + p3 + p4

    # ---- Round 3 · The Fee Market (250) ------------------------------------
    p, m = grade_choice(ans.get("q31"), MCQ_BASEFEE[1], 70)
    detail.append(("R3", "Who receives the EIP-1559 base fee", ans.get("q31"), MCQ_BASEFEE[1], p, 70, m))
    p2, m2 = grade_numeric(ans.get("q32"), K["eth_fee_usd"], 70, rel_tol=0.25)
    detail.append(("R3", "Cost of a 21,000-gas ETH transfer (USD)", ans.get("q32"), f"${K['eth_fee_usd']:.5f}", p2, 70, m2))
    p3, m3 = grade_choice(ans.get("q33"), MCQ_SOLFEE[0], 50)
    detail.append(("R3", "A 5,000-lamport SOL base fee in USD", ans.get("q33"), MCQ_SOLFEE[0], p3, 50, m3))
    line = (ans.get("q34") or "").strip()
    p4 = 60 if len(line.split()) >= 8 else (30 if len(line.split()) >= 4 else 0)
    m4 = "submitted" if p4 == 60 else ("too short — half credit" if p4 else "no answer")
    detail.append(("R3", "The 2140 security question (written)", line[:60], "graded live by a director", p4, 60, m4))
    r3 = p + p2 + p3 + p4

    return r1, r2, r3, r1 + r2 + r3, detail


# ----------------------------------------------------------------------------
# UI HELPERS
# ----------------------------------------------------------------------------

def inject_css():
    st.markdown(f"""<style>
      .stApp {{ background:{NAVY}; }}
      .vca-hero {{ border-left:5px solid {GOLD}; padding:.45rem 0 .45rem 1rem; margin-bottom:1rem; }}
      .vca-hero h1 {{ color:{INK}; font-size:1.75rem; margin:0; letter-spacing:-.02em; }}
      .vca-hero p  {{ color:#9FB0C9; margin:.25rem 0 0; font-size:.9rem; }}
      .vca-card {{ background:#13294F; border:1px solid #244472; border-radius:10px;
                   padding:1rem 1.15rem; margin-bottom:.9rem; }}
      .vca-tag {{ display:inline-block; background:{GOLD}; color:{NAVY}; font-weight:700;
                  font-size:.7rem; letter-spacing:.09em; padding:.2rem .55rem;
                  border-radius:4px; text-transform:uppercase; }}
      .vca-tag-teal {{ background:{TEAL}; color:#fff; }}
      .vca-big {{ font-size:2.6rem; font-weight:800; color:{GOLD}; line-height:1; }}
      .vca-mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; color:{TEAL}; }}
      .vca-muted {{ color:#9FB0C9; font-size:.86rem; }}
      div[data-testid="stMetricValue"] {{ color:{GOLD}; }}
      .stButton>button {{ background:{GOLD}; color:{NAVY}; font-weight:700; border:0;
                          border-radius:7px; padding:.55rem 1.1rem; }}
      .stButton>button:hover {{ background:#d8b877; color:{NAVY}; }}
      a {{ color:{TEAL}; }}
    </style>""", unsafe_allow_html=True)


def hero(title, sub):
    st.markdown(f"<div class='vca-hero'><h1>{title}</h1><p>{sub}</p></div>",
                unsafe_allow_html=True)


def countdown(seconds, label):
    """Client-side countdown in a component iframe (st.markdown strips <script>).
    Purely visual: it never reruns the app, so nothing typed is ever lost."""
    import streamlit.components.v1 as components
    components.html(f"""
      <style>
        body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
        .w {{ background:#13294F; border:1px solid #244472; border-radius:10px;
              padding:.75rem 1rem; display:flex; align-items:center; gap:.9rem;
              flex-wrap:wrap; }}
        .t {{ background:{GOLD}; color:{NAVY}; font-weight:700; font-size:.7rem;
              letter-spacing:.09em; padding:.25rem .6rem; border-radius:4px;
              text-transform:uppercase; white-space:nowrap; }}
        #cd {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
               font-size:1.6rem; font-weight:700; color:{TEAL}; }}
        .m {{ color:#9FB0C9; font-size:.8rem; }}
      </style>
      <div class="w">
        <span class="t">{label}</span>
        <span id="cd">--:--</span>
        <span class="m">timer is a guide — the presenter calls time</span>
      </div>
      <script>
        var end = Date.now() + {seconds}*1000;
        var el = document.getElementById('cd');
        (function tick(){{
          var s = Math.max(0, Math.round((end - Date.now())/1000));
          el.textContent = Math.floor(s/60) + ':' + String(s%60).padStart(2,'0');
          if (s <= 30) el.style.color = '#E2574C';
          if (s > 0) setTimeout(tick, 250);
        }})();
      </script>""", height=86)


# ----------------------------------------------------------------------------
# STUDENT VIEW
# ----------------------------------------------------------------------------

def student_view():
    L = get_lock()
    K = answer_key(L)
    ss = st.session_state
    ss.setdefault("step", 0)
    ss.setdefault("ans", {})
    ss.setdefault("t0", None)

    hero("VCA WEEK 3 · BLOCK HUNT",
         "Bitcoin and Blockchain Mechanics — Monday 14 September 2026 · 1,000 points · 3 rounds")

    # ---- Step 0 · join ------------------------------------------------------
    if ss.step == 0:
        st.markdown(f"""<div class='vca-card'>
          <span class='vca-tag'>Before you start</span>
          <p style='margin:.7rem 0 .3rem'>Open a second tab on
          <b>mempool.space</b>. You will read real numbers off a real block —
          nothing in this game can be answered from the slides.</p>
          <p class='vca-muted'>Three rounds, about 11 minutes. Your score counts
          toward the 15% in-session participation component.</p>
        </div>""", unsafe_allow_html=True)

        c1, c2 = st.columns(2)
        name = c1.text_input("Full name")
        email = c2.text_input("Villanova email", placeholder="you@villanova.edu")
        if st.button("Start Round 1", type="primary"):
            if not name.strip() or "@" not in email:
                st.error("Name and a valid email are required — this is how participation is scored.")
            else:
                ss.name, ss.email = name.strip(), email.strip().lower()
                ss.t0 = time.time()
                ss.step = 1
                st.rerun()
        return

    # ---- Step 1 · Round 1 ---------------------------------------------------
    if ss.step == 1:
        countdown(240, "Round 1 · The Block · 400 pts")
        st.markdown(f"""<div class='vca-card'>
          <span class='vca-tag-teal vca-tag'>Everyone uses the same block</span>
          <div class='vca-big' style='margin:.5rem 0'>#{L['height']:,}</div>
          <p>Go to <a href='https://mempool.space/block/{L['height']}' target='_blank'>
          mempool.space/block/{L['height']}</a> and read the numbers off the page.</p>
          <p class='vca-muted'>Miner revenue for a block = the subsidy (new coins the
          protocol creates) + the fees users paid to be included. Find both.</p>
        </div>""", unsafe_allow_html=True)

        a = ss.ans
        a["q11"] = st.number_input("1. Block subsidy — new BTC created by this block",
                                   value=a.get("q11"), step=0.001, format="%.4f",
                                   help="The protocol's issuance, not the total reward.")
        a["q12"] = st.number_input("2. Total fees paid in this block (BTC)",
                                   value=a.get("q12"), step=0.0001, format="%.5f",
                                   help="mempool.space shows this as 'Total fees'. Convert from sats if needed: 100,000,000 sats = 1 BTC.")
        a["q13"] = st.number_input("3. Number of transactions in this block",
                                   value=a.get("q13"), step=1.0, format="%.0f")
        a["q14"] = st.number_input("4. Fees as a % of total miner revenue",
                                   value=a.get("q14"), step=0.01, format="%.2f",
                                   help="fees / (subsidy + fees) x 100")
        if st.button("Lock Round 1 → Round 2", type="primary"):
            ss.step = 2
            st.rerun()
        return

    # ---- Step 2 · Round 2 ---------------------------------------------------
    if ss.step == 2:
        countdown(210, "Round 2 · The Schedule · 350 pts")
        st.markdown(f"""<div class='vca-card'>
          <span class='vca-tag'>No explorer needed — this is arithmetic</span>
          <p style='margin-top:.6rem'>Known: the next halving is at block
          <b class='vca-mono'>{HALVING_BLOCK:,}</b> · the network targets
          <b class='vca-mono'>{BLOCKS_PER_DAY}</b> blocks a day ·
          circulating supply is
          <b class='vca-mono'>{L['circulating_btc']:,.0f} BTC</b>.</p>
        </div>""", unsafe_allow_html=True)

        a = ss.ans
        a["q21"] = st.number_input("5. How many blocks until the next halving?",
                                   value=a.get("q21"), step=1.0, format="%.0f")
        a["q22"] = st.number_input("6. New BTC issued per year at the current subsidy",
                                   value=a.get("q22"), step=1.0, format="%.0f",
                                   help="subsidy x blocks per day x 365")
        a["q23"] = st.number_input("7. That issuance as a % of circulating supply",
                                   value=a.get("q23"), step=0.01, format="%.2f",
                                   help="This is Bitcoin's inflation rate. Gold's is roughly 1.5%.")
        a["q24"] = st.radio(
            "8. Difficulty re-targets every 2,016 blocks to hold block time near 10 minutes. "
            "If global hashrate doubles tomorrow and stays there, BTC issued this year…",
            MCQ_HASHRATE,
            index=MCQ_HASHRATE.index(a["q24"]) if a.get("q24") in MCQ_HASHRATE else None)
        if st.button("Lock Round 2 → Round 3", type="primary"):
            ss.step = 3
            st.rerun()
        return

    # ---- Step 3 · Round 3 ---------------------------------------------------
    if ss.step == 3:
        countdown(210, "Round 3 · The Fee Market · 250 pts")
        st.markdown(f"""<div class='vca-card'>
          <span class='vca-tag'>Bitcoin pays miners. Ethereum burns. Solana is cheap.</span>
          <p style='margin-top:.6rem'>Locked for this session: ETH base fee
          <b class='vca-mono'>{L['eth_base_fee_gwei']:g} gwei</b> ·
          ETH <b class='vca-mono'>${L['eth_price']:,.0f}</b> ·
          SOL <b class='vca-mono'>${L['sol_price']:,.2f}</b>.
          A plain ETH transfer costs <b class='vca-mono'>{ETH_TRANSFER_GAS:,}</b> gas.
          1 gwei = 0.000000001 ETH.</p>
        </div>""", unsafe_allow_html=True)

        a = ss.ans
        a["q31"] = st.radio("9. Under EIP-1559, who receives the base fee?", MCQ_BASEFEE,
                            index=MCQ_BASEFEE.index(a["q31"]) if a.get("q31") in MCQ_BASEFEE else None)
        a["q32"] = st.number_input("10. Cost of that 21,000-gas ETH transfer, in USD",
                                   value=a.get("q32"), step=0.00001, format="%.5f",
                                   help="base fee (gwei) x 1e-9 x 21,000 x ETH price")
        a["q33"] = st.radio("11. Solana charges 5,000 lamports per signature "
                            "(1 SOL = 1,000,000,000 lamports). In USD that is roughly…",
                            MCQ_SOLFEE,
                            index=MCQ_SOLFEE.index(a["q33"]) if a.get("q33") in MCQ_SOLFEE else None)
        a["q34"] = st.text_area(
            "12. Today the subsidy is roughly 98% of what a Bitcoin miner earns. "
            "In 2140 the subsidy is zero. In one line: what has to be true for "
            "Bitcoin to still be secure then?",
            value=a.get("q34", ""), height=90,
            placeholder="One sentence. A director reads these out loud.")

        if st.button("Submit and see my score", type="primary"):
            r1, r2, r3, total, detail = score_submission(a, L)
            elapsed = round(time.time() - (ss.t0 or time.time()), 1)
            save_result({
                "email": ss.email, "name": ss.name, "score": total,
                "r1": r1, "r2": r2, "r3": r3, "answers": a,
                "line": (a.get("q34") or "").strip(),
                "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "elapsed": elapsed,
            })
            ss.result = (r1, r2, r3, total, detail, elapsed)
            ss.step = 4
            st.rerun()
        return

    # ---- Step 4 · results ---------------------------------------------------
    r1, r2, r3, total, detail, elapsed = ss.result
    pct = total / 10.0
    st.markdown(f"""<div class='vca-card' style='text-align:center'>
        <span class='vca-tag'>Submitted · {ss.name}</span>
        <div class='vca-big' style='font-size:4rem;margin:.5rem 0'>{total}</div>
        <p class='vca-muted'>out of 1,000 &nbsp;·&nbsp; {pct:.0f}% &nbsp;·&nbsp; {elapsed/60:.1f} min</p>
      </div>""", unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("R1 · The Block", f"{r1}/400")
    c2.metric("R2 · The Schedule", f"{r2}/350")
    c3.metric("R3 · The Fee Market", f"{r3}/250")

    st.markdown("#### Where the points went")
    df = pd.DataFrame(
        [{"Rd": d[0], "Question": d[1], "You": d[2], "Answer": d[3],
          "Pts": f"{d[4]}/{d[5]}", "": d[6]} for d in detail])
    st.dataframe(df, hide_index=True, use_container_width=True)

    st.markdown("#### The three things this was built to show")
    st.markdown(f"""
- **Miner revenue is still {100 - K['fee_share_pct']:.1f}% newly printed coins.** Bitcoin's
  security is currently paid for by dilution of existing holders, not by the people using it.
- **Hashrate does not change issuance.** Difficulty re-targets to hold the schedule.
  More miners buys more security at the same coin cost — it does not buy more coins.
- **The three chains charge for blockspace in three different ways.** BTC pays the miner,
  ETH burns the base fee and tips the validator, SOL prices blockspace near zero.
  Week 6 turns this into the difference between *fees* and *revenue*.
""")
    st.caption("Your score is recorded. Nothing else to submit.")


# ----------------------------------------------------------------------------
# PRESENTER VIEW
# ----------------------------------------------------------------------------

def make_qr(url):
    """PNG bytes, or None if the qrcode library isn't installed on the host.
    Never raises: a missing QR must not take down the presenter view."""
    try:
        import qrcode
        img = qrcode.make(url, box_size=11, border=2)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


@st.fragment(run_every="4s")
def live_board():
    rows = all_results()
    st.markdown(f"#### Live leaderboard &nbsp;<span class='vca-muted'>"
                f"{len(rows)} submitted</span>", unsafe_allow_html=True)
    if not rows:
        st.info("No submissions yet. Students appear here the moment they submit.")
        return
    df = pd.DataFrame(rows, columns=["Name", "Email", "Score", "R1", "R2", "R3",
                                     "Line", "Submitted", "Min"])
    df.insert(0, "#", range(1, len(df) + 1))
    df["Min"] = (df["Min"] / 60).round(1)
    st.dataframe(df[["#", "Name", "Score", "R1", "R2", "R3", "Min"]],
                 hide_index=True, use_container_width=True, height=380)
    c1, c2, c3 = st.columns(3)
    c1.metric("Median score", f"{int(df['Score'].median())}")
    c2.metric("Top score", f"{int(df['Score'].max())}")
    c3.metric("Avg R1 (the live pull)", f"{df['R1'].mean():.0f}/400")


def presenter_view():
    L = get_lock()
    K = answer_key(L)
    hero("PRESENTER · VCA WEEK 3 BLOCK HUNT",
         "Lock the block → project the QR → run the rounds → export scores")

    tabs = st.tabs(["1 · Lock & QR", "2 · Live board", "3 · Answer key", "4 · Export"])

    # ---- Lock ---------------------------------------------------------------
    with tabs[0]:
        st.markdown("##### Step 1 — set the numbers everyone is graded against")
        st.caption("Lock a block BEFORE you send students in. A new block lands every "
                   "~10 minutes; without a lock, students in minute 2 and minute 9 "
                   "would be looking at different data.")

        if st.button("Fetch live from mempool.space + CoinGecko"):
            try:
                blk = fetch_btc_block()
                st.session_state.fetched = blk
                st.session_state.fetched.update(fetch_supply_and_prices())
                st.success(f"Pulled block #{blk['height']:,} "
                           f"({blk['tx_count']:,} txs, {blk['total_fees_btc']} BTC fees"
                           + (f", mined by {blk['pool']}" if blk.get("pool") else "") + ")")
            except Exception as e:
                st.warning(f"Live fetch failed ({type(e).__name__}). Type the numbers "
                           f"in by hand from your own screen — everything below still works.")

        f = st.session_state.get("fetched", {})
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Bitcoin — the block**")
            height = st.number_input("Block height", value=int(f.get("height", L["height"])), step=1)
            subsidy = st.number_input("Block subsidy (BTC)", value=float(f.get("subsidy", L["subsidy"])),
                                      step=0.001, format="%.4f")
            fees = st.number_input("Total fees in block (BTC)",
                                   value=float(f.get("total_fees_btc", L["total_fees_btc"])),
                                   step=0.0001, format="%.5f")
            txc = st.number_input("Transactions in block", value=int(f.get("tx_count", L["tx_count"])), step=1)
            circ = st.number_input("Circulating BTC", value=float(f.get("circulating_btc", L["circulating_btc"])),
                                   step=1.0, format="%.0f")
        with c2:
            st.markdown("**Prices and the ETH fee market**")
            btcp = st.number_input("BTC price (USD)", value=float(f.get("btc_price", L["btc_price"])), step=1.0)
            ethp = st.number_input("ETH price (USD)", value=float(f.get("eth_price", L["eth_price"])), step=1.0)
            solp = st.number_input("SOL price (USD)", value=float(f.get("sol_price", L["sol_price"])), step=0.01)
            basefee = st.number_input("ETH base fee (gwei) — read off etherscan.io/gastracker",
                                      value=float(L["eth_base_fee_gwei"]), step=0.01, format="%.3f")

        if st.button("LOCK THESE NUMBERS", type="primary"):
            cfg_set("lock", {
                "height": int(height), "subsidy": float(subsidy),
                "total_fees_btc": float(fees), "tx_count": int(txc),
                "circulating_btc": float(circ), "btc_price": float(btcp),
                "eth_price": float(ethp), "sol_price": float(solp),
                "eth_base_fee_gwei": float(basefee),
                "locked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "locked": 1,
            })
            st.success("Locked. Every student from now on is graded against these numbers.")
            st.rerun()

        if L.get("locked"):
            st.info(f"Currently locked → block **#{L['height']:,}** · fees "
                    f"**{L['total_fees_btc']} BTC** · fee share "
                    f"**{K['fee_share_pct']:.2f}%** · locked at {L.get('locked_at','')} UTC")
        else:
            st.warning("NOT LOCKED — the app is serving fallback numbers from 14 Sep 2026. "
                       "Lock before class starts.")

        st.divider()
        st.markdown("##### Step 2 — project this QR")
        default_url = cfg_get("base_url", os.environ.get("BASE_URL", ""))
        url = st.text_input("Student play URL (paste this app's public address)",
                            value=default_url, placeholder="https://your-app.streamlit.app")
        if url and url != default_url:
            cfg_set("base_url", url)
        if url:
            qc1, qc2 = st.columns([1, 1])
            with qc1:
                qr = make_qr(url)
                if qr:
                    st.image(qr, caption=url, width=330)
                else:
                    from urllib.parse import quote
                    st.image(
                        "https://api.qrserver.com/v1/create-qr-code/"
                        f"?size=330x330&data={quote(url, safe='')}",
                        caption=url, width=330)
                    st.caption("QR drawn by an external service (the qrcode "
                               "library isn't installed on this host).")
                st.markdown(
                    f"<div class='vca-card' style='text-align:center'>"
                    f"<span class='vca-muted'>or have them type</span><br>"
                    f"<span class='vca-mono' style='font-size:1.15rem;font-weight:700'>"
                    f"{url.replace('https://', '').replace('http://', '')}</span></div>",
                    unsafe_allow_html=True)
            with qc2:
                st.markdown(f"""<div class='vca-card'>
                  <span class='vca-tag'>Say this out loud</span>
                  <p style='margin-top:.6rem'>"Phones out. Scan this. Put
                  <b>mempool.space</b> in a second tab. You are about to read a real
                  Bitcoin block that was mined in the last ten minutes, and the numbers
                  on your screen are the ones I am grading."</p>
                </div>""", unsafe_allow_html=True)
        else:
            st.caption("Paste the deployed URL above and a QR appears here.")

    # ---- Board --------------------------------------------------------------
    with tabs[1]:
        live_board()
        st.caption("Refreshes every 4 seconds. Leave this tab on the projector.")

    # ---- Key ----------------------------------------------------------------
    with tabs[2]:
        st.markdown(f"""
### Round 1 · The Block — 400 pts
| # | Question | Answer |
|---|---|---|
| 1 | Block subsidy | **{K['subsidy']:.3f} BTC** |
| 2 | Total fees in block | **{K['fees']:.5f} BTC** |
| 3 | Transactions | **{K['tx_count']:,}** |
| 4 | Fees as % of miner revenue | **{K['fee_share_pct']:.2f}%** |

**Talking point.** Total miner revenue for that block was {K['miner_rev']:.4f} BTC,
about \\${K['miner_rev']*L['btc_price']:,.0f}. Only {K['fee_share_pct']:.2f}% of it came
from users. The other {100-K['fee_share_pct']:.2f}% was printed. *Ask the room: who is
actually paying for Bitcoin's security right now?* Answer: every existing holder, through
dilution. That is the Week 3 concept — issuance is a cost borne by existing holders — and
it is the same idea as share issuance diluting an equity holder.

### Round 2 · The Schedule — 350 pts
| # | Question | Answer |
|---|---|---|
| 5 | Blocks to next halving | **{K['blocks_left']:,}** (block {HALVING_BLOCK:,} − {L['height']:,}) |
| 6 | New BTC per year | **{K['annual_btc']:,.0f} BTC** ({L['subsidy']}×{BLOCKS_PER_DAY}×365) |
| 7 | As % of circulating | **{K['annual_pct']:.2f}%** |
| 8 | Hashrate doubles → issuance | **Roughly unchanged** |

**Talking point.** {K['blocks_left']:,} blocks at {BLOCKS_PER_DAY}/day is about
{K['days_left']:,.0f} days — roughly April 2028. At that point annual issuance halves to
~{K['annual_btc']/2:,.0f} BTC and the inflation rate falls to ~{K['annual_pct']/2:.2f}%,
below gold's ~1.5%. Q8 is the one most students miss: **hashrate has no effect on issuance.**
Difficulty re-targets every 2,016 blocks to hold 10-minute blocks. More hashrate buys more
security at the *same* coin cost. This is the difference between a supply schedule that is
fixed in code and one that responds to demand — the exact thing that made the Week 2
FDV−MC gap matter.

### Round 3 · The Fee Market — 250 pts
| # | Question | Answer |
|---|---|---|
| 9 | Who gets the EIP-1559 base fee | **Burned — no one receives it** |
| 10 | 21,000-gas ETH transfer | **\\${K['eth_fee_usd']:.5f}** ({K['eth_fee_eth']:.9f} ETH) |
| 11 | 5,000-lamport SOL fee | **~\\${K['sol_fee_usd']:.5f} — about \\$0.001** |
| 12 | Written line | graded live |

**Talking point.** Three chains, three answers to the same question. Bitcoin hands the whole
fee to the miner. Ethereum destroys the base fee and tips the validator only the priority fee,
so heavy use makes ETH *scarcer*. Solana prices blockspace near zero and makes it up on volume.
*Ask: if a chain burns its fees, is that revenue?* Park the argument — it is the whole of Week 6.

**Reading Q12 out loud.** The answer you want: fees have to replace the subsidy, which means
either far higher fee volume or far higher fee prices per block. Students who say "the price
goes up" have missed it — price does not pay miners, blockspace demand does. This is the
first real bear case they will meet and it is the bridge to the Week 6 fee waterfall.
""")

    # ---- Export -------------------------------------------------------------
    with tabs[3]:
        rows = all_results()
        if rows:
            df = pd.DataFrame(rows, columns=["Name", "Email", "Score", "R1", "R2", "R3",
                                             "Line", "SubmittedUTC", "ElapsedSec"])
            df["Participation"] = 60 + (40 * df["Score"] / 1000).round(1)
            st.dataframe(df, hide_index=True, use_container_width=True)
            st.download_button("Download CSV for the gradebook",
                               df.to_csv(index=False).encode(),
                               f"vca_week3_scores_{datetime.now().strftime('%Y%m%d')}.csv",
                               "text/csv", type="primary")
            st.caption("Participation column is already converted to the 60–100 scale "
                       "(60 + 40 × score/1000). Paste it into the tracker.")
            st.divider()
            st.markdown("##### The written lines (Q12) — read a few out loud")
            for n, l in df[["Name", "Line"]].values:
                if str(l).strip():
                    st.markdown(f"- **{n}** — {l}")
        else:
            st.info("No submissions yet.")
        st.divider()
        with st.expander("Danger zone"):
            if st.button("Wipe all submissions"):
                clear_results()
                st.warning("Cleared.")


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def main():
    is_presenter = st.query_params.get("p") == "present"
    st.set_page_config(page_title="VCA Week 3 · Block Hunt",
                       page_icon="B",
                       layout="wide" if is_presenter else "centered")
    inject_css()
    get_conn()
    if is_presenter:
        presenter_view()
    else:
        student_view()


if __name__ == "__main__":
    main()
