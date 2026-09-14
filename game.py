import csv, io, sqlite3, threading
from datetime import datetime, timezone
import streamlit as st

BLOCK = 967034
SUBSIDY = 3.125
FEES = 0.035775
CIRC = 20_084_409
HALVING = 1_050_000
MINER_REV = SUBSIDY + FEES
FEE_SHARE = FEES / MINER_REV * 100
BLOCKS_LEFT = HALVING - BLOCK
ANNUAL = SUBSIDY * 144 * 365
ANNUAL_PCT = ANNUAL / CIRC * 100

DB = "w3.db"
LK = threading.Lock()
_C = None


def conn():
    global _C
    if _C is None:
        with LK:
            if _C is None:
                c = sqlite3.connect(DB, check_same_thread=False, timeout=15)
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("PRAGMA busy_timeout=8000")
                c.execute("""CREATE TABLE IF NOT EXISTS r(
                    email TEXT PRIMARY KEY, name TEXT, score INT,
                    a1 TEXT,a2 TEXT,a3 TEXT,a4 TEXT,a5 TEXT,a6 TEXT,a7 TEXT,
                    line TEXT, ts TEXT)""")
                c.commit()
                _C = c
    return _C


def save(rec):
    c = conn()
    with LK:
        c.execute("""INSERT INTO r VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(email) DO UPDATE SET name=excluded.name,score=excluded.score,
            a1=excluded.a1,a2=excluded.a2,a3=excluded.a3,a4=excluded.a4,
            a5=excluded.a5,a6=excluded.a6,a7=excluded.a7,
            line=excluded.line,ts=excluded.ts""", rec)
        c.commit()


def rows():
    c = conn()
    with LK:
        return c.execute("SELECT name,email,score,a1,a2,a3,a4,a5,a6,a7,line,ts "
                         "FROM r ORDER BY score DESC, ts ASC").fetchall()


def band(v, target, pts, tol):
    if v is None:
        return 0
    try:
        v = float(v)
    except Exception:
        return 0
    d = abs(v - target)
    if d <= tol:
        return pts
    if d <= tol * 2:
        return pts // 2
    return 0


def grade(a):
    s = 0
    s += band(a[0], SUBSIDY, 150, 0.001)
    s += band(a[1], FEES, 150, FEES * 0.12)
    s += 100 if (a[2] and float(a[2]) > 0) else 0
    s += band(a[3], FEE_SHARE, 200, 0.25)
    s += band(a[4], BLOCKS_LEFT, 150, 2)
    s += band(a[5], ANNUAL, 125, ANNUAL * 0.03)
    s += band(a[6], ANNUAL_PCT, 125, 0.06)
    return s


def css():
    st.markdown("""<style>
    .stApp{background:#0B1E3D;}
    h1,h2,h3,h4,p,label,span,div,li,td,th{color:#F2F4F8!important;}
    .bx{background:#13294F;border:1px solid #244472;border-radius:10px;padding:14px 18px;margin-bottom:12px;}
    .big{font-size:2.6rem;font-weight:800;color:#C6A15B!important;line-height:1.1;}
    .gold{color:#C6A15B!important;font-weight:700;}
    .stButton>button{background:#C6A15B;color:#0B1E3D!important;font-weight:700;border:0;border-radius:7px;padding:10px 20px;}
    </style>""", unsafe_allow_html=True)


def student():
    ss = st.session_state
    st.markdown("### VCA WEEK 3 · BLOCK HUNT")
    st.markdown(f"<div class='bx'>Open <b>mempool.space</b> and search block "
                f"<span class='big'>{BLOCK}</span><br>"
                f"<a href='https://mempool.space/block/{BLOCK}' target='_blank' "
                f"style='color:#1B8F8A'>mempool.space/block/{BLOCK}</a><br><br>"
                f"Miner revenue = subsidy (new coins) + fees users paid. Find both."
                f"</div>", unsafe_allow_html=True)

    if ss.get("done"):
        sc = ss["score"]
        st.markdown(f"<div class='bx' style='text-align:center'>"
                    f"<div class='big' style='font-size:4rem'>{sc}</div>"
                    f"<p>out of 1,000 &nbsp;·&nbsp; {ss['nm']}</p></div>",
                    unsafe_allow_html=True)
        st.markdown(f"""
| # | Question | Answer |
|---|---|---|
| 1 | Block subsidy | **{SUBSIDY} BTC** |
| 2 | Total fees | **{FEES} BTC** |
| 3 | Transactions | completion credit |
| 4 | Fees % of miner revenue | **{FEE_SHARE:.2f}%** |
| 5 | Blocks to halving | **{BLOCKS_LEFT:,}** |
| 6 | New BTC per year | **{ANNUAL:,.0f}** |
| 7 | Issuance % of supply | **{ANNUAL_PCT:.2f}%** |
""")
        st.markdown(f"**{100-FEE_SHARE:.2f}% of that miner's revenue was newly "
                    f"printed coins.** Bitcoin's security is paid for by diluting "
                    f"existing holders, not by the people using it.")
        st.success("Recorded. Nothing else to submit.")
        return

    c1, c2 = st.columns(2)
    nm = c1.text_input("Full name")
    em = c2.text_input("Villanova email")

    q = []
    q.append(st.text_input("1. Block subsidy — new BTC created by this block"))
    q.append(st.text_input("2. Total fees paid in this block (BTC)"))
    q.append(st.text_input("3. Number of transactions in this block"))
    q.append(st.text_input("4. Fees as a % of total miner revenue  ·  fees ÷ (subsidy + fees) × 100"))
    q.append(st.text_input(f"5. Blocks until the next halving  ·  it happens at block {HALVING:,}"))
    q.append(st.text_input("6. New BTC issued per year  ·  subsidy × 144 blocks/day × 365"))
    q.append(st.text_input(f"7. That issuance as a % of circulating supply  ·  {CIRC:,} BTC"))
    line = st.text_area("8. Today the subsidy is ~99% of what a miner earns. In 2140 it is zero. "
                        "In one line: what has to be true for Bitcoin to still be secure?", height=80)

    if st.button("Submit"):
        if not nm.strip() or "@" not in em:
            st.error("Name and email are required — this is how participation is scored.")
            return
        clean = [x.strip().replace(",", "").replace("%", "").replace("$", "") or None for x in q]
        sc = grade(clean)
        if line.strip():
            sc = min(1000, sc)
        save((em.strip().lower(), nm.strip(), sc, *[x or "" for x in clean], line.strip(),
              datetime.now(timezone.utc).isoformat(timespec="seconds")))
        st.session_state.update(done=True, score=sc, nm=nm.strip())
        st.rerun()


def presenter():
    st.markdown("### PRESENTER · WEEK 3 BLOCK HUNT")
    r = rows()
    st.markdown(f"**{len(r)} submitted** · block {BLOCK}")
    if st.button("Refresh"):
        st.rerun()

    if r:
        md = "| # | Name | Score | Participation |\n|---|---|---|---|\n"
        for i, x in enumerate(r, 1):
            md += f"| {i} | {x[0]} | {x[2]} | {60 + 40*x[2]/1000:.1f} |\n"
        st.markdown(md)

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Name", "Email", "Score", "Participation", "Q1", "Q2", "Q3",
                    "Q4", "Q5", "Q6", "Q7", "Written", "SubmittedUTC"])
        for x in r:
            w.writerow([x[0], x[1], x[2], round(60 + 40*x[2]/1000, 1),
                        x[3], x[4], x[5], x[6], x[7], x[8], x[9], x[10], x[11]])
        st.download_button("Download CSV for the gradebook", buf.getvalue(),
                           f"vca_week3_{datetime.now().strftime('%Y%m%d')}.csv", "text/csv")

        st.markdown("#### Written lines — read a few out loud")
        for x in r:
            if x[10]:
                st.markdown(f"- **{x[0]}** — {x[10]}")
    else:
        st.info("No submissions yet.")

    st.divider()
    st.markdown(f"""#### Answer key · block {BLOCK}
| # | Answer |
|---|---|
| 1 | **{SUBSIDY} BTC** — mempool shows "Subsidy + fees"; subtract the fees out |
| 2 | **{FEES} BTC** |
| 3 | completion credit |
| 4 | **{FEE_SHARE:.2f}%** = {FEES} ÷ {MINER_REV:.6f} × 100 |
| 5 | **{BLOCKS_LEFT:,}** = {HALVING:,} − {BLOCK:,} |
| 6 | **{ANNUAL:,.0f} BTC** = {SUBSIDY} × 144 × 365 |
| 7 | **{ANNUAL_PCT:.2f}%** |

**Point 1.** {100-FEE_SHARE:.2f}% of that miner's revenue was newly printed coins.
Security is paid for by diluting holders, not by users.

**Point 2.** If hashrate doubled tomorrow, issuance would not change. Difficulty
re-targets every 2,016 blocks. More hashrate buys more security at the same coin cost.

**Point 3.** BTC pays the miner, ETH burns the base fee and tips the validator,
SOL prices blockspace near zero. Three answers to the same question. That is Week 6.
""")


st.set_page_config(page_title="VCA Week 3", page_icon="B", layout="centered")
css()
conn()
if st.query_params.get("p") == "present":
    presenter()
else:
    student()
