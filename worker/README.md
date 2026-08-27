# The chat

A Cloudflare Worker that answers questions about the day's published list. It
is deployed on its own. The Action, Pages and the daily deploy do not know it
exists, and the page renders its ten names whether or not this is running.

What it does on each turn: check the passphrase, check a daily counter in KV,
fetch `latest.json` from the public page, send that file to Gemini as the
system instruction, stream the answer back.

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
improve its products. So it matters what passes through: a passphrase, her
question, and the day's list — which is already public, at a URL anyone can
open. There is no account, no position and no holding anywhere in the request,
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

### 5. The two secrets

```bash
npx wrangler secret put GEMINI_API_KEY   # the key from step 1
npx wrangler secret put PASSPHRASE       # invent one, a few plain words
```

Secrets live in Cloudflare. Neither is ever committed, and neither reaches the
browser.

The passphrase is not a login — there is no account here and nothing to steal.
It stops a stranger who finds the Worker URL from using up her day.

### 6. Point the page at it

In `site/app.js`, set:

```javascript
const CHAT_URL = "https://put-screen-chat.<subdomain>.workers.dev/";
```

Commit and push. The push redeploys the page on its own.

### 7. Give her the link

```
https://liug88.github.io/tradingscreening/?k=<the passphrase>
```

Bookmark that. The page stores the passphrase for the session and strips it
from the address bar, so she never types it and it is not sitting in the URL
while she reads.

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

A JSON object with `output_text` in it means the key works and every field name
in `src/index.js` is still current. An error naming a field means Google has
moved something, and that name is what to fix.

Then the Worker itself:

```bash
npx wrangler dev
```

and, from another terminal:

```bash
curl -X POST http://localhost:8787/ \
  -H 'Content-Type: application/json' \
  -H 'Origin: https://liug88.github.io' \
  -d '{"key":"<passphrase>","messages":[{"role":"user","content":"Why did the top name rank first?"}]}'
```

Three answers worth reading before you trust it:

- a plain one — "why did UNH rank first?"
- an absence — "why isn't NVDA on the list?", which is what the bench is for
- **"should I sell this put?"** — this must come back as what the numbers say
  and what the risks are. If it recommends anything, the prompt has drifted.

Then check a wrong passphrase is refused, and that `DAILY_TURNS` stops the run
once it is used up.

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
carries a passphrase and a question. The day's data is fetched server-side from
the same public file the page reads, and `store: false` on every call asks
Google not to keep the conversation. There is nowhere in this to put anything
about her, which is deliberate.
