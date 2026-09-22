# Insider Trades

An app for your phone that tracks the publicly disclosed stock and crypto trades
of Trump administration officials, members of Congress you choose, and insiders
at Trump-linked companies — and checks whether copying them would actually have
made money.

Everything comes from official public filings. Every figure links back to the
document it came from. Nothing is invented, estimated, or filled in.

---

## Contents

1. [What it does](#1-what-it-does)
2. [What it honestly cannot do](#2-what-it-honestly-cannot-do) ← **read this one**
3. [Setup, step by step](#3-setup-step-by-step)
4. [Installing it on your iPhone](#4-installing-it-on-your-iphone)
5. [Editing the watchlist](#5-editing-the-watchlist)
6. [Fixing a broken source](#6-fixing-a-broken-source)
7. [How the copy check works](#7-how-the-copy-check-works)
8. [What it costs](#8-what-it-costs)
9. [Checking the data yourself](#9-checking-the-data-yourself)
10. [Running it on your own computer](#10-running-it-on-your-own-computer)

---

## 1. What it does

Every 30 minutes a free GitHub robot wakes up, downloads the latest filings,
reads them, and publishes clean data to a web page you keep on your home screen.

**Four sources:**

| Source | What it gives you |
|---|---|
| **U.S. House Clerk** | Periodic Transaction Reports — the trades your chosen members of Congress disclose |
| **SEC EDGAR Form 4** | Insider trades at Trump Media (DJT), with exact share counts, prices, **and current holdings** |
| **whitehouse.gov** | OGE Form 278-T filings by executive-branch officials |
| **Blockscout** | On-chain activity for the World Liberty Financial multisig and Trump's labelled wallet, with airdrop spam filtered out |

**Four screens:**

- **Feed** — newest trades first. Who, what, buy or sell, how much, when they
  traded, when they told us, and how many days they sat on it.
- **People** — everyone being tracked; tap for their full history and any
  holdings the filings actually report.
- **Copy check** — for each disclosed buy, what the stock did afterwards
  compared to the S&P 500.
- **Sources** — whether each source is working, and when it last succeeded.

You get a phone notification when someone on your watchlist files a new trade.

---

## 2. What it honestly cannot do

This is the most important section. Skip it and you will misread the app.

### Trump's own filings cannot be read by a computer

The President's OGE 278-T filings are published as **photographs of paper**, some
of them 48 MB. A few have a text layer added by scanning software, and that layer
is badly mangled — the word "Purchase" comes out as `ourchose`, `lourchaso`,
`PUrchaso`, and `$500,001` loses its comma to become `$500 001`.

A parser that tried its best on that would produce rows that *look* real and
aren't. So it refuses. His January 2026 filing contains 185 transactions; the app
publishes **zero** of them and shows the document instead, marked
*"scanned filing — not machine-parsed"*, with a link to read it yourself.

**This is deliberate.** Showing you a PDF is honest. Showing you a confident
number that might be wrong is not.

### His trades are also mostly not copyable anyway

Even when readable, the President's filings are overwhelmingly **municipal bonds
and Treasury notes**. Those have no ticker, no free price history, and you can't
meaningfully "copy" a specific muni bond. The copy check simply doesn't apply.

The copy check works well for Congress members' stock trades, DJT insider trades,
and crypto — not for him.

### The Senate is not included

The Senate's eFD system requires you to personally affirm the Ethics in
Government Act prohibitions before searching, and it actively blocks automated
access. That affirmation is yours to make, not a script's. Rather than work
around it, the app leaves the Senate out and says so on the Sources screen.

If you want Senate data, search it yourself at
<https://efdsearch.senate.gov/search/> — the terms permit personal,
non-commercial use, which is what you're doing.

### Most Form 4 rows are not trades

Of the 107 Form 4 rows for Trump Media, only **36** are open-market buys or
sells. The rest are share grants from the company (35) and shares withheld to
pay tax (28). Those aren't decisions anyone could copy, so the app labels them
and leaves them out of the copy check. It still shows them, because they tell
you a lot about who's being paid in stock.

### Amounts are ranges, not numbers

Congressional and executive filings disclose a **band** — "$15,001 – $50,000" —
not an exact figure. The app always shows the band exactly as filed and never
converts it to a single number. This also means the copy check can measure
*returns* but never *how much money* anyone made.

### This is not investment advice

It's a record of public filings plus a backtest of them. Past returns say
nothing about future ones, and a disclosed trade is 1–45 days old before you
ever see it. I'm not a licensed financial adviser and neither is this app.

---

## 3. Setup, step by step

You need a free GitHub account. Nothing else, and nothing that can charge you.

### Step 1 — Put the code on GitHub

Create a **public** repository (public matters — see
[What it costs](#8-what-it-costs)), then from this folder:

```bash
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPO.git
git branch -M main
git push -u origin main
```

### Step 2 — Turn on GitHub Pages

In your repository: **Settings → Pages → Build and deployment → Source** and
choose **GitHub Actions**. That's the whole step.

### Step 3 — Allow the robot to save data

**Settings → Actions → General → Workflow permissions** → select
**Read and write permissions** → **Save**.

Without this the robot can fetch filings but can't store them.

### Step 4 — Tell the SEC who you are

The SEC requires a real contact email with every request. It's good manners and
it's their published rule.

**Settings → Secrets and variables → Actions → New repository secret**

- Name: `CONTACT_EMAIL`
- Value: your email address

### Step 5 — Set up phone notifications

1. Install **ntfy** from the App Store (free, no account).
2. Invent a long, unguessable topic name — treat it like a password, because
   anyone who knows it can read your alerts. For example:
   `insider-trades-k7m2x9qp4w`
3. In the ntfy app: **+ → Subscribe to topic** → enter that name.
4. Back on GitHub, add a second secret:
   - Name: `NTFY_TOPIC`
   - Value: the same topic name

### Step 6 — Start it

**Actions → Update data → Run workflow.**

The first run takes 10–20 minutes because it downloads every filing once. After
that it only fetches new ones and finishes in well under a minute.

When it's done, your app is live at:

```
https://YOUR-USERNAME.github.io/YOUR-REPO/
```

### Step 7 (optional but recommended) — The dead man's switch

The app already turns red when data goes stale, and a watchdog job checks four
times a day. But if GitHub Actions stopped running *entirely*, nothing inside
GitHub could tell you.

For a true external check, sign up free at <https://healthchecks.io>, create a
check expecting a ping every hour, and add its ping URL as a third secret named
`HEALTHCHECK_URL`. It emails you if the pings stop.

---

## 4. Installing it on your iPhone

1. Open **Safari** (it must be Safari — Chrome on iOS can't install web apps).
2. Go to `https://YOUR-USERNAME.github.io/YOUR-REPO/`
3. Tap the **Share** button (the square with an arrow).
4. Scroll down and tap **Add to Home Screen**.
5. Tap **Add**.

It now behaves like a normal app: its own icon, no browser bars, works offline
(it'll tell you the data is stale rather than pretend otherwise).

Notifications come through the **ntfy** app, not this one. That's deliberate —
iOS web notifications are unreliable, and ntfy just works.

---

## 5. Editing the watchlist

Everything you'd want to change lives in one file: **`watchlist.yml`**. Edit it
on GitHub directly (click the file, then the pencil icon) and commit. The next
run picks it up automatically.

### Choosing which members of Congress

It is currently set to follow **every** House member who files — 108 of them
filed 396 reports in 2026:

```yaml
congress:
  track_all: true
```

That gives the fullest picture and a large enough sample that more people clear
the copy check's 5-decision threshold. Notifications stay manageable because of
the alert threshold below, not because the list is short.

To follow only specific people instead, set `track_all: false` and list them:

```yaml
congress:
  track_all: false
  members:
    - { last: Pelosi, first: Nancy, district: CA11 }
```

Use their **last name and district**. To find the district, open
<https://disclosures-clerk.house.gov/PublicDisclosure> and search for them.

### Choosing which trades are worth a notification

```yaml
alerts:
  min_amount_usd: 50001
```

Only trades of at least this much will notify you. With every House member
tracked this matters a lot — most disclosures are in the `$1,001 – $15,000`
band, which you do not want waking you up.

Because filings disclose a **band** rather than a figure, the test is on the
**bottom** of the band:

| Disclosed band | Alerts at `50001`? |
|---|---|
| `$1,001 – $15,000` | no |
| `$15,001 – $50,000` | no — it tops out a dollar short |
| `$50,001 – $100,000` | **yes** |
| `$1,000,001 – $5,000,000` | **yes** |

SEC Form 4 reports an exact value, so that number is used directly. On-chain
**swaps** always notify, because they're rare, deliberate, and have no dollar
value to compare. Airdrops and plain transfers never do.

Set `min_amount_usd: 0` to be told about everything. Anything held back is
counted on the **Sources** screen, so it's never silently dropped.

### Adding another company

```yaml
companies:
  - name: Trump Media & Technology Group Corp.
    ticker: DJT
    cik: "0001849635"
```

Find the CIK number by searching the company at
<https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany>.

### Adding a crypto wallet

Two are already set up, both verified against Etherscan's own public name tags:

| Wallet | Address | Label |
|---|---|---|
| World Liberty Financial multisig | `0x5be9a495…` | "World Liberty: Multisig" |
| Donald Trump | `0x94845333…` | "Donald Trump" (attribution originally by Arkham) |

To add another:

```yaml
crypto_wallets:
  - address: "0x1234...."
    name: Some Entity
    chain: ethereum
    label_source: "Etherscan public name tag"
    label_source_url: "https://etherscan.io/address/0x1234...."
```

**`label_source` is required.** The app refuses any wallet without one, and shows
it next to every transaction — so you always know *who says* this wallet belongs
to this person. Never add an address because someone claimed it on social media.

Supported chains: `ethereum`, `base`, `polygon`, `optimism`.

#### Two traps worth knowing about

**A token contract is not a wallet.** Searching for "World Liberty address"
turns up the WLFI and USD1 *token contracts*. Adding one would report every
transfer of that token, by anyone on earth, as a Trump-linked trade. Always
check the address is not a contract first — those two are listed in
`watchlist.yml` as deliberately excluded.

**Most activity on a famous wallet is spam.** Anyone can send any token to any
address, and scammers do it constantly to manufacture an association. Of the 50
most recent transfers into Trump's labelled wallet, **46 were unsolicited** —
tokens named `FAFO`, `pwease`, `4CHAN`, `TMAGA`. The app flags a transfer as a
likely airdrop when the token arrived unprompted and the wallet has never sent
that token, keeps it out of the copy check, and says so on the card.

What survives that filter is real: two genuine DEX swaps by Trump's wallet, and
33 treasury movements by the multisig.

Note also that a token transfer is **not** the same as a trade. Moving coins
between your own wallets looks much like buying on an exchange. The app only
treats a transfer as a buy or sell when the blockchain shows it was a swap.

### Other settings

```yaml
copy_check_horizons: [7, 30, 90]   # days measured after a trade becomes public
min_trades_for_stats: 5            # below this, no win rate is shown
alerts:
  only_watchlisted: true           # false = alert on every trade found
```

---

## 6. Fixing a broken source

Sources break. Websites change their layout, a government server goes down. The
app is built so that one broken source never takes down the rest — it shows
"unavailable" with its last good timestamp instead.

### Step 1 — Look at the Sources screen

Open the app, tap **Sources**. Each one says OK or FAILING, when it last
succeeded, and what it last did.

### Step 2 — Read the run log

Go to **Actions** in your repository and open the most recent "Update data" run.
Each source is its own step, so the broken one is obvious.

### Step 3 — Try it on your own machine

See [section 10](#10-running-it-on-your-own-computer), then run just the broken
source, for example:

```bash
python sources/house_ptr.py
```

The error message will usually tell you what changed.

### Common problems and what they mean

| What you see | What it means | What to do |
|---|---|---|
| `HTTP 403` or `Access Denied` | The site is blocking automated requests | Usually temporary. If it persists, the site added a bot check and that source may need rethinking. |
| `HTTP 404` on a filing | A document was moved or withdrawn | Normally harmless; it'll disappear on the next run. |
| `scanned_image` on lots of filings | Those filers submitted paper | Working as intended. Open the PDF yourself. |
| `no rows parsed` on a filing that looks fine | The form's layout changed | The parser needs updating — see below. |
| Everything fails at once | No internet in the runner, or GitHub is down | Check <https://www.githubstatus.com>. |

### If a form's layout changed

1. Download the filing that won't parse.
2. Dump what the parser actually sees:
   ```bash
   python -c "import sys; sys.path.insert(0,'sources'); from house_ptr import extract_text; print(extract_text(open('thefile.pdf','rb').read()))"
   ```
3. Compare that with the PDF and adjust the pattern in the relevant file under
   `sources/`.
4. Save the filing into `tests/fixtures/` and add a test, so it can't silently
   break again.
5. Bump `PARSER_VERSION` at the top of that source file. That tells the app to
   re-read every filing with the improved parser, instead of only new ones.

### Re-reading everything from scratch

**Actions → Update data → Run workflow**, and tick **force_reparse**.

---

## 7. How the copy check works

The question it answers: *if I had read this filing the moment it became public
and bought the same thing, what would have happened?*

- **Starting point** — the closing price on the first trading day **strictly
  after** the disclosure date. Not the day the insider traded — you couldn't
  have known. Not the disclosure day itself — you might have read it after the
  close.
- **Measured at** +7, +30 and +90 days, and up to today.
- **Compared against** the S&P 500 (SPY) for stocks, Bitcoin for crypto, over
  the exact same window. A 10% gain in a month when the market rose 12% is not
  a win.
- **Buys only.** "Would copying this sale have made money" has no answer unless
  you know what you were holding.

### Where it refuses to answer

- **Fewer than 5 measurable buys** → no win rate. A percentage from three trades
  is noise dressed up as insight.
- **No price history** → reported as unmeasurable, never as zero.
- **No price near the disclosure** → refused. One real filing disclosed a thinly
  traded stock whose first available price was *389 days later*; using it would
  have measured a completely different trade.
- **Options, bonds, grants, tax withholding** → excluded, each with the reason
  shown on the card.

### The trap to watch for

Buying twenty different stocks on one day is **one decision about one day's
market**, not twenty independent calls — but it looks like a 20-trade track
record. One official in the current data has 28 measured buys that all became
public on the *same day*, showing a 67.9% win rate.

The app flags this in orange wherever it happens. When you see it, treat that
person's record as a single data point. For contrast, Cleo Fields' 23 decisions
are spread across 16 separate dates, which means considerably more.

---

## 8. What it costs

**Nothing. There is no payment method anywhere in this setup.**

| What | Cost | The limit that could bite |
|---|---|---|
| GitHub Actions | £0 | **Unlimited for public repositories.** Private repos get 2,000 minutes/month, and a 30-minute schedule would exceed that — which is why Step 1 says public. |
| GitHub Pages | £0 | 100 GB/month bandwidth. Your app is ~3 MB. |
| SEC EDGAR | £0 | 10 requests/second. The app self-limits to 5. |
| House Clerk | £0 | No published limit; the app is deliberately slow and polite. |
| Yahoo Finance | £0 | Unofficial and undocumented. If it ever stops, prices show as unavailable and nothing else breaks. |
| CoinGecko | £0 | ~30 calls/minute. The app uses a handful per day. |
| Blockscout | £0 | No API key required. |
| ntfy.sh | £0 | No account. |
| healthchecks.io | £0 | Optional. Free tier covers 20 checks. |

Your repository being public means your **watchlist is public** too. It contains
only the names of public officials and publicly labelled wallet addresses —
nothing private. But don't put anything personal in `watchlist.yml`.

Secrets (`CONTACT_EMAIL`, `NTFY_TOPIC`, `HEALTHCHECK_URL`) are **not** public.
GitHub encrypts them and they never appear in the code or the logs.

---

## 9. Checking the data yourself

Don't take the app's word for it. This prints random parsed trades next to the
raw text of the filings they came from:

```bash
python scripts/audit_sample.py 10
```

Every trade shows what was stored, what the original document says, and a link
to verify. Add `--seed 42` to get the same sample twice.

This is worth doing — running it is how four real bugs were found, including one
that was reporting six jointly-held positions as a member's own, and one that
invented a 380-day disclosure delay that never happened.

If you find a number that disagrees with its filing, that's a bug. The filing is
always right.

The parser tests run against real filings kept in `tests/fixtures/`:

```bash
python tests/run_all.py
```

---

## 10. Running it on your own computer

You need Python 3.11 or newer.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Mac or Linux

pip install -r requirements.txt
```

Then, in order:

```bash
python sources/house_ptr.py        # House PTRs
python sources/sec_form4.py        # SEC Form 4
python sources/exec_278t.py        # Executive branch 278-T
python sources/crypto_wallets.py   # Crypto wallets
python sources/prices.py           # Check price providers are alive
python sources/build_feed.py       # Merge into the feed
python sources/copy_check.py       # Work out the returns
```

To view it locally:

```bash
mkdir -p _site && cp -r web/* _site/ && mkdir -p _site/data && cp data/*.json _site/data/
python -m http.server 8765 --directory _site
```

Then open <http://localhost:8765>.

### Where everything lives

```
watchlist.yml          the only file you normally edit
sources/               one module per source, so they can be swapped
  common.py            shared fetching, rate limits, per-source health
  house_ptr.py         House Clerk PTRs
  sec_form4.py         SEC Form 4 and 144
  exec_278t.py         executive-branch 278-T
  crypto_wallets.py    on-chain wallets
  prices.py            free price history
  build_feed.py        merges sources, decides what's copyable
  copy_check.py        the returns calculation
  alerts.py            ntfy notifications
web/                   the app itself — plain HTML, CSS and JavaScript
data/                  the published data (written by the robot)
tests/                 parser tests, run against real filings
scripts/audit_sample.py  the accuracy checker
```

There is no build step and no framework. `web/app.js` is a single file you can
read and edit.

---

## A note on the rules this follows

- Every figure links to its original filing.
- Disclosure ranges are shown exactly as filed and never converted to a number.
- A filing that can't be read reliably is shown as a document, never guessed at.
- A source that fails says "unavailable" with its last good time, rather than
  showing nothing or showing something stale as if it were current.
- Statistics that rest on too little data are withheld, not published small.
- Sources that ask you to affirm something personally are left for you to do.
