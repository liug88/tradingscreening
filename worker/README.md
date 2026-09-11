# The chat

A Cloudflare Worker that answers questions about the day's published list. It
is deployed on its own. The Action, Pages and the daily deploy do not know it
exists, and the page renders its ten names whether or not this is running.

What it does on each turn: check the request came from the page, check a daily
counter in KV, fetch `latest.json` from the public page, send that file to
Gemini as the system instruction, stream the answer back.

What it will not do is advise. The rules are in `RULES` at the top of
`src/index.js` — it explains what the screen found and leaves the decision with
her. If you change that prompt, that is the line to keep.

It runs on Gemini's free tier. Nothing here is billed, there is no card on file
and no cap to watch. When the day's allowance runs out the chat says to try
again later; the list on the page is unaffected either way.

## Setting it up

You need a Cloudflare account (the free plan is enough) and a Gemini API key.
Both are free. Ten minutes, once.

### 1. Get a key

[aistudio.google.com/apikey](https://aistudio.google.com/apikey) → **Create API
key**. Sign in with a Google account and take the free tier; do not enable
billing. Without billing there is no way for this to cost anything — the worst
case is a refused request.

While you are there, open the **rate limits** page and note the daily request
limit for the model in `wrangler.toml`. Step 3 wants a number under it.

Then add the same key to the GitHub repo as `GEMINI_API_KEY` (**Settings →
Secrets and variables → Actions**), so the morning run and the chat use one
key. The morning run makes a single call a day.

**What Google gets.** On the free tier, Google may use what passes through to
improve its products. So it matters what passes through: her question and the
day's list — which is already public, at a URL anyone can open. There is no
account, no position and no holding anywhere in the request,
and nowhere in the shape of it to put one. That is the same rule the rest of
this repo follows, and it is why the free tier is usable here at all.

### 2. Install and log in

```bash
cd worker
npm install
npx wrangler login
```

### 3. The counter

```bash
npx wrangler kv namespace create COUNTER
```

It prints an id. Paste it into `wrangler.toml`, replacing
`REPLACE_WITH_KV_NAMESPACE_ID`. Set `DAILY_TURNS` in the same file to something
under the free-tier limit from step 1.

### 4. Deploy

```bash
npx wrangler deploy
```

It prints a URL like `https://put-screen-chat.<subdomain>.workers.dev`. Keep
it — step 6 needs it.

### 5. The one secret

```bash
npx wrangler secret put GEMINI_API_KEY   # the key from step 1
```

It lives in Cloudflare. It is never committed and never reaches the browser.

There used to be a `PASSPHRASE` here as well. It is gone — see **No passphrase**
below. If you set one on an older deploy, `npx wrangler secret delete
PASSPHRASE` clears it; nothing reads it either way.

### 6. Point the page at it

In `site/app.js`, set:

```javascript
const CHAT_URL = "https://put-screen-chat.<subdomain>.workers.dev/";
```

Commit and push. The push redeploys the page on its own.

### 7. Give her the link

```
https://liug88.github.io/tradingscreening/
```

Bookmark that. Nothing else. The chat is on the page.

### No passphrase

There was one, carried in a `?k=` parameter on a link meant to be bookmarked.
It broke the only promise this project makes. The page took the parameter out
of the address bar on load and kept it in `sessionStorage`, so a bookmark saved
from the open page carried no passphrase, and `sessionStorage` was gone by the
next morning anyway. The chat then rendered *absent* — not disabled, not
explained — and she had no way to know why.

It was never a login. `PRODUCT.md` asks for no login and zero install, and one
person uses this. What is left is the origin check — the request has to come
from the published page — and `DAILY_TURNS`.

Be clear about what that is: **`DAILY_TURNS` is one counter for the whole
Worker per day, not one per visitor.** The URL is in a public repo. Anyone who
finds it and posts from the right origin can spend the day's questions before
she asks her first, and the same free-tier quota feeds the morning screen.
Raising the number is a one-line change in `wrangler.toml` if it ever happens.

## Checking it

Check the key and the request shape first, straight against Google. This is the
one test that catches a renamed field, and it takes a second:

```bash
curl -sS -X POST "https://generativelanguage.googleapis.com/v1beta/interactions" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H 'Content-Type: application/json' \
  -H 'Api-Revision: 2026-05-20' \
  -d '{"model":"gemini-3.7-flash","system_instruction":"Answer in three words.","input":"Say hello.","store":false}'
```

On Windows PowerShell, put the body in a file rather than fighting the quoting:

```powershell
'{"model":"gemini-3.7-flash","system_instruction":"Answer in three words.","input":"Say hello.","store":false}' | Set-Content -Encoding ascii "$env:TEMPody.json"
curl.exe -sS -X POST "https://generativelanguage.googleapis.com/v1beta/interactions" -H "x-goog-api-key: $env:GEMINI_API_KEY" -H "Content-Type: application/json" -H "Api-Revision: 2026-05-20" -d "@$env:TEMPody.json"
```

What comes back is `"status":"completed"` and a `steps` array. The answer is the
`model_output` step, behind a `thought` step that carries a signature and no
content. There is no `output_text`, whatever the docs say — `_text()` in
`catalyst.py` tries it first and then walks the timeline, and the walk is the
path that runs. An error naming a field means Google has moved something, and
that name is what to fix.

This only checks the non-streaming shape. The Worker reads the streamed one —
`step.delta` events with `delta.type == "text"` — which `wrangler dev` in the
next section is what actually exercises.

Then the Worker itself:

```bash
npx wrangler dev
```

and, from another terminal:

```bash
curl -X POST http://localhost:8787/ \
  -H 'Content-Type: application/json' \
  -H 'Origin: https://liug88.github.io' \
  -d '{"messages":[{"role":"user","content":"Why did the top name rank first?"}]}'
```

Three answers worth reading before you trust it:

- a plain one — "why did UNH rank first?"
- an absence — "why isn't NVDA on the list?", which is what the bench is for
- **"should I sell this put?"** — this must come back as what the numbers say
  and what the risks are. If it recommends anything, the prompt has drifted.

Then drop the `Origin` header and check it comes back 403, and let
`DAILY_TURNS` run out and check it says so.

## What it costs

Nothing. The free tier has no card behind it, so the ceiling is a rate limit
rather than a bill. Two limits apply: Google's, which you read in step 1, and
`DAILY_TURNS` in `wrangler.toml`, which trips first and says so in plain words.
Cloudflare's free plan covers the Worker and the counter.

If the morning run ever starts failing on a 429, that is the daily key doing
double duty — raise the limit, or give the Action its own key.

## Turning it off

Set `CHAT_URL = ""` in `site/app.js` and push. The panel stops appearing and
the rest of the page is unaffected. `npx wrangler delete` removes the Worker
itself.

## What never passes through it

No account, no positions, no holdings, no personal information. The request
carries a question and nothing else. The day's data is fetched server-side from
the same public file the page reads, and `store: false` on every call asks
Google not to keep the conversation. There is nowhere in this to put anything
about her, which is deliberate.
