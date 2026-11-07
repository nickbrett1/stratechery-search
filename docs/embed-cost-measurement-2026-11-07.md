# Embedding cost of the whole corpus — measured, 2026-11-07

**Question:** what does one pass over all 29,918 passages cost with
`cohere/embed-v5.0`, measured rather than guessed?

**Answer:** **≈ 7.9M – 8.2M input tokens → ≈ $0.95 – $0.98 at $0.12 / 1M tokens.**
Budget **8M tokens / ~$1.00** for one full pass, plus a rounding error for
retries. It is not a meaningful line item; the wall-clock and the ops around it
are the real cost.

Nothing was embedded. No request was sent to `/v1/embeddings`, `/v1/rerank`, or
any inference endpoint; no tokens were billed. The only network calls to the
gateway were `GET /model/info`, `GET /openapi.json`, and the free
`POST /utils/token_counter` local estimate.

---

## 1. What was reachable

| Thing | Status |
|---|---|
| Live index on ryzen | **Reachable**, read-only, at `/srv/docker/stratechery-search/data/index/search.sqlite` (root-owned, mode 644, 160,776,192 bytes, built 2026-11-06 21:45). Opened by `ryzen-goose` over A2A as `sqlite3.connect("file:…?mode=ro", uri=True)`; `journal_mode=delete`, so no `-wal`/`-shm` side files were created. |
| Service on ryzen | `https://ryzen.tail86fd19.ts.net/stats` → 200, reports `chunks: 29918`, `articles: 1691`, `last: 2026-11-04`. Matches. |
| LiteLLM gateway | `http://192.168.1.69:4000`, healthy, `cohere/embed-v5.0` listed with `mode: embedding`, `max_input_tokens: 128000`. |
| SSH to ryzen from this container | **Not available** (publickey denied for `vscode@ryzen`). The DB was read via the `ryzen-goose` A2A agent instead — it is the same host, the same file, and the counts reproduce the service's own `/stats` exactly (29,918 / 1,691). |
| Cohere tokenizer | **Not obtainable.** See §3. |

The corpus was **not** substituted: every count below comes from the live
`search.sqlite` on ryzen, and the 100 sampled `chunk_id`s resolve to real
`chunks` rows whose `text_sha256` the agent verified.

## 2. The sample (100 passages, stratified by era)

Population counts by year, straight from the index (chunks joined to `articles`
on `message_id`, year taken from `substr(sent_at,1,4)`):

| year | chunks | articles | chars |
|---|---|---|---|
| 2016 | 6 | 1 | 7,921 |
| 2017 | 610 | 51 | 751,964 |
| 2018 | 1,784 | 156 | 2,186,130 |
| 2019 | 2,167 | 184 | 2,667,735 |
| 2020 | 2,723 | 193 | 3,320,871 |
| 2021 | 2,694 | 201 | 3,267,978 |
| 2022 | 3,834 | 201 | 4,639,447 |
| 2023 | 4,125 | 180 | 5,016,953 |
| 2024 | 4,366 | 186 | 5,384,456 |
| 2025 | 4,278 | 181 | 5,287,942 |
| 2026 | 3,331 | 157 | 4,135,122 |
| **total** | **29,918** | **1,691** | **36,666,519** |

Every chunk is `chunker_version = 1`. Mean 1,225.6 chars, min 129, max 1,544.

**Bands** (the eras that matter: pre-paywall-era posts, the 2021–22 plateau of
the suspected `text/plain` padding, then the modern era):

| band | chunks | true share |
|---|---|---|
| 2016-2020 | 7,290 | 24.37 % |
| 2021-2022 | 6,528 | 21.82 % |
| 2023-2024 | 8,491 | 28.38 % |
| 2025-2026 | 7,609 | 25.43 % |

**Draw:** allocation proportional to true chunk counts by largest remainder
(24 / 22 / 28 / 26, sums to 100 — i.e. sample shares within 1.8 pp of true
shares), then `random.Random(20261107).sample()` of **index positions** within
each band over the chunk list ordered by `chunks.id`, without replacement. The
seed and the ordered population make the draw exactly reproducible. Each sampled
row was re-fetched by `id` and its `text_sha256` checked.

Per-passage token counts for all 100 rows are in the appendix.

## 3. Tokenizer — what was actually measured with

Three things were tried, in order of proximity to `cohere/embed-v5.0`:

1. **The gateway's own counter — reachable, but it is not Cohere's.** `POST /utils/token_counter` with `{"model":"cohere/embed-v5.0"}` answers:
   `{"total_tokens":10,"model_used":"embed-v5.0-pro","tokenizer_type":"openai_tokenizer","count_source":"local_estimate"}`.
   That is a decisive negative result: the gateway has **no Cohere tokenizer**
   for this alias — it falls back to an OpenAI tokenizer for its own estimates.
   So "ask the gateway" is not a better measurement, it is the same
   approximation with less control.
2. **Cohere's API tokenizer — not reachable.** `POST /v1/tokenize` → 404. The
   Cohere pass-through forms (`/cohere/tokenize`, `/cohere/v1/tokenize`) answer
   `model 'cohere/embed-v5.0' not found`. Cohere has never published the BPE
   used by `embed-english-v3` / Command R; the only Cohere *Lineage* tokenizer
   on Hugging Face is the XLM-R **WordPiece** vocabulary behind
   `CohereLabs/multilingual-22-12` (`tokenizer.json`, sha256 `a1f892d…5507b7`,
   10,624,029 bytes, vocab 501,153). It is Aya's base vocabulary, **not** the
   production embedder's tokenizer.
3. **tiktoken — used as the primary.** `cl100k_base` (also `o200k_base` for
   sensitivity), tiktoken 0.14.0, exactly as `len(enc.encode(text))`.

So: **the headline number is tiktoken `cl100k_base`, labelled an approximation.**
The Cohere-lineage WordPiece count is reported alongside as a one-sided sanity
check, and it says the error runs the way you would expect:

> Cohere-lineage / cl100k = **1.0398** overall (+4.0 %). cl100k **under**counts
> this corpus. The ratio drifts with era (1.0307 in 2016–20 → 1.0463 in
> 2025–26), so the undercount is worse for recent, denser-to-typography text.

That is the direction to carry into a budget: **tiktoken is a floor, not a
ceiling.** `o200k_base` is within 0.5 % of `cl100k_base` (262.25 vs 263.51 mean),
so the choice of OpenAI encoding does not matter; the Cohere gap does.

## 4. Distribution

All 100 passages, `cl100k_base`:

| metric | cl100k | o200k | Cohere-lineage | chars |
|---|---|---|---|---|
| mean | 263.51 | 262.25 | 274.00 | 1,242.5 |
| median | 266.0 | 265.5 | 275.5 | 1,251.5 |
| p95 | 332.1 | 329.2 | 350.1 | 1,479.2 |
| max | 364 | 361 | 378 | 1,494 |
| min | 160 | 159 | 160 | 707 |
| sd | 42.0 | 41.6 | 44.3 | — |

Tokens per character: 212.1 per 1,000 chars (cl100k), 220.5 (Cohere-lineage).

### Per-era breakdown — and the ASCII-padding finding does **not** reproduce

| band | n | mean chars | mean tok (cl100k) | sd | median | p95 | max | tok/char | Cohere/cl100k |
|---|---|---|---|---|---|---|---|---|---|
| 2016-2020 | 24 | 1,210.1 | 246.04 | 37.8 | 252 | 302.6 | 326 | 0.2033 | 1.0307 |
| 2021-2022 | 22 | 1,302.8 | 268.18 | 32.6 | 271 | 313.6 | 326 | 0.2058 | 1.0358 |
| 2023-2024 | 28 | 1,239.2 | 269.04 | 43.0 | 276 | 335.6 | 340 | 0.2171 | 1.0441 |
| 2025-2026 | 26 | 1,224.7 | 269.73 | 47.0 | 268 | 341.0 | 364 | 0.2202 | 1.0463 |

This is the most useful thing in the measurement, and it corrects the premise in
the brief. Measured over ~500 passages per band (1,535 total, systematic
`[::k]` stride within each band), the ASCII-table signature is **absent**:

| band | box-drawing chars / 1k chars | `|` / 1k chars | double spaces / 1k chars | chunks containing `"   "` |
|---|---|---|---|---|
| 2016-2020 | 0.00 | 0.02 | 0.00 | 0 / 7,290 |
| 2021-2022 | 0.00 | 0.09 | 0.00 | 0 / 6,528 |
| 2023-2024 | 0.00 | 0.06 | 0.00 | 0 / 8,491 |
| 2025-2026 | 0.00 | 0.08 | 0.00 | 0 / 7,609 |

Zero box-drawing characters and not one chunk with three consecutive spaces in
the whole index. Two reasons the padding cannot show up in *passage length*
either: the chunker caps a chunk at ~1,544 chars, so length is bounded by
construction (mean chars per band is flat, 1,303 max in 2021-22 vs 1,210 in
2016-20, +7.7 %), and the stored text is a normalised flattening, not the raw
`text/plain` part.

What **does** exist, and is the real era effect, is **token density**: 0.2033 →
0.2202 tokens per character, **+8.3 %** from the oldest band to the newest
(+6.9 % on mean tokens per chunk, 250.9 → 268.3 on the 1,535-passage sample).
So a flat mean over the whole corpus would be wrong — but it is wrong by ~7–8 %
across eras, not by the factor the padding story implied, and because chunk
length is capped the whole distribution is tight (sd ≈ 16 % of the mean; no
passage in the sample exceeds 378 tokens).

## 5. Extrapolation to 29,918 passages

Weighted by each band's **true** share of passages, not the sample's:

| band | sample mean (cl100k) | × true chunks | tokens |
|---|---|---|---|
| 2016-2020 | 246.04 | 7,290 | 1,793,644 |
| 2021-2022 | 268.18 | 6,528 | 1,750,691 |
| 2023-2024 | 269.04 | 8,491 | 2,284,382 |
| 2025-2026 | 269.73 | 7,609 | 2,052,381 |
| **total** | | **29,918** | **7,881,098** |

Apply the Cohere-lineage ratio per band: **8,194,478 tokens**.

Two independent checks:

- **Wider sample.** The 1,535-passage per-band systematic subsample gives
  band means 250.87 / 250.97 / 265.08 / 268.34 (cl100k) → weighted total
  **7,759,768**, i.e. **1.5 % below** the 100-sample figure. That is the
  sampling error of n=22–28 per band, and it is the honest uncertainty band on
  the point estimate.
- **Flat mean.** Because the allocation is proportional to the population, the
  weighted total (7,881,098) and the naive flat mean × 29,918 (7,883,692) agree
  to 0.03 %. Worth stating plainly: stratification buys the per-era numbers and
  a smaller variance, **not** a different total, when the strata are allocated
  proportionally. A flat mean is only badly wrong if it comes from a *uniform*
  (non-stratified) draw that under-represents the modern bands.

**Figure for the design note: 7.9M tokens (tiktoken) / 8.2M (Cohere proxy);
call it ≈ 8M, ±1.5 % sampling.**

## 6. Price and total spend

Read from the gateway's own model registry (`GET /model/info`), not from memory:

```json
{"model_name": "cohere/embed-v5.0",
 "litellm_params": {"model": "cohere/embed-v5.0-pro"},
 "model_info": {"input_cost_per_token": 1.2e-07, "max_input_tokens": 128000}}
```

**$0.12 per 1M input tokens** (`1.2e-07 × 1e6`). Corroborated by the public
LiteLLM price list (`model_prices_and_context_window.json`, entry
`cohere/embed-v5.0-pro`: `input_cost_per_token: 1.2e-07`) — and note the alias
`cohere/embed-v5.0` resolves to the **pro** model, not the cheap one:
`cohere/embed-v5.0-fast` is `8e-08` = **$0.08 / 1M**.

| | tokens | cost |
|---|---|---|
| cl100k measurement | 7,881,098 | **$0.9457** |
| Cohere-lineage correction | 8,194,478 | **$0.9833** |
| wider-sample check (cl100k × $0.12) | 7,759,768 | $0.9312 |

**One full pass ≈ $0.95, or ~$0.98 on the conservative figure. Call it $1.00
with retries and a rebuild.** If the cheap model would do, a pass is $0.63.

## 7. Batching, and therefore wall-clock

- **Yes, the gateway takes a batch.** `POST /v1/embeddings` schema is OpenAI's:
  `input` is `{"type": "array", "items": {"type": "string"}}` with **no
  `maxItems`** — the gateway imposes no cap of its own.
- **The binding limit is Cohere's: 96 texts per request.** At 29,918 passages
  that is `ceil(29918 / 96) = **312 requests**`. Per-text ceiling is
  `max_input_tokens: 128000` from `/model/info`; the longest passage in the
  corpus is 1,544 chars ≈ 340 tokens, so text length never binds.
- 312 requests is minutes of wall-clock, not hours. For the design note: the
  pass is bounded by request latency (`312 × ~0.3–1 s ≈ 1.5–5 min`) far more
  than by money. No rate-limit or 429 behaviour was probed — that would need
  real traffic.

## 8. Rerank (separate question, one line each)

- **Endpoint exists**: `/v1/rerank`, `/v2/rerank` and `/rerank` are in the
  gateway's `openapi.json`; `GET /v1/rerank` → 405, `POST {}` → `400 "/rerank:
  Missing required parameter: 'query'"`. So the route is live and Cohere-shaped.
- **But no rerank model is registered**: `/model/info` lists nine models and
  none is `mode: rerank`. It would need a model entry before it can be used from
  this gateway.
- **Price (LiteLLM public price list, `model_prices_and_context_window.json`)**:
  `rerank-v5.0-pro` `input_cost_per_token: 5e-08` = **$0.05 / 1M tokens**;
  `rerank-v5.0-fast` `2e-08` = **$0.02 / 1M tokens**. The older per-query
  generation is `rerank-v3.5` at `input_cost_per_query: 0.002` = **$2 / 1k
  searches** (= $2,000 / 1M search units), per Cohere's "1 search unit = 1 query
  with up to 100 documents".

## 9. Caveats, in the order they could bite

1. **tiktoken is the measurement, not the model's tokenizer.** Under-counts by
   ~4 % (Cohere-lineage check). Already priced into the $0.98 figure.
2. **±1.5 % sampling error** at n=22–28 per band (wider-sample check). Irrelevant
   at this price; would matter only if someone tried to make a decision out of
   the difference between $0.95 and $0.98.
3. **`chunker_version = 1` everywhere**, so this measures *this* chunking. A
   re-chunk changes the token count roughly one-for-one with passage count and
   length — a bigger chunk size means fewer passages but the same text, so the
   total is dominated by 36.7M chars, not by 29,918.
4. **Prices are the gateway's registry, not an invoice.** A Cohere-side price
   change, or a different alias, moves the total linearly.

---

## Appendix — the 100 sampled passages

`toks_cl100k` = tiktoken `cl100k_base`, `toks_cohere` = Cohere-lineage WordPiece
(`CohereLabs/multilingual-22-12`), `chars` = `length(chunks.text)`.

| # | band | chunk_id | msg | ord | chars | cl100k | o200k | cohere |
|---|---|---|---|---|---|---|---|---|
| 1 | 2016-2020 | 69357:2 | 69357 | 2 | 1190 | 238 | 237 | 239 |
| 2 | 2016-2020 | 70112:16 | 70112 | 16 | 1150 | 220 | 220 | 232 |
| 3 | 2016-2020 | 70250:48 | 70250 | 48 | 1494 | 326 | 324 | 337 |
| 4 | 2016-2020 | 70316:18 | 70316 | 18 | 707 | 160 | 159 | 160 |
| 5 | 2016-2020 | 74707:13 | 74707 | 13 | 1215 | 258 | 258 | 269 |
| 6 | 2016-2020 | 74707:18 | 74707 | 18 | 1309 | 266 | 262 | 271 |
| 7 | 2016-2020 | 75601:2 | 75601 | 2 | 1228 | 266 | 268 | 280 |
| 8 | 2016-2020 | 76457:4 | 76457 | 4 | 1335 | 265 | 265 | 268 |
| 9 | 2016-2020 | 76609:4 | 76609 | 4 | 1422 | 283 | 278 | 293 |
| 10 | 2016-2020 | 77098:7 | 77098 | 7 | 1247 | 250 | 249 | 248 |
| 11 | 2016-2020 | 77703:15 | 77703 | 15 | 1011 | 226 | 225 | 239 |
| 12 | 2016-2020 | 78156:6 | 78156 | 6 | 978 | 200 | 199 | 206 |
| 13 | 2016-2020 | 78166:6 | 78166 | 6 | 1225 | 255 | 255 | 260 |
| 14 | 2016-2020 | 79673:7 | 79673 | 7 | 1213 | 239 | 239 | 246 |
| 15 | 2016-2020 | 80068:14 | 80068 | 14 | 938 | 197 | 197 | 197 |
| 16 | 2016-2020 | 80997:4 | 80997 | 4 | 1263 | 236 | 237 | 244 |
| 17 | 2016-2020 | 83738:2 | 83738 | 2 | 1403 | 303 | 301 | 322 |
| 18 | 2016-2020 | 83896:1 | 83896 | 1 | 1040 | 206 | 206 | 218 |
| 19 | 2016-2020 | 84965:4 | 84965 | 4 | 1385 | 300 | 300 | 297 |
| 20 | 2016-2020 | 86112:2 | 86112 | 2 | 1066 | 216 | 218 | 220 |
| 21 | 2016-2020 | 86679:1 | 86679 | 1 | 1331 | 257 | 256 | 272 |
| 22 | 2016-2020 | 87736:6 | 87736 | 6 | 1093 | 202 | 203 | 214 |
| 23 | 2016-2020 | 89291:8 | 89291 | 8 | 1399 | 278 | 278 | 294 |
| 24 | 2016-2020 | 90476:0 | 90476 | 0 | 1400 | 258 | 256 | 260 |
| 25 | 2021-2022 | 53007:9 | 53007 | 9 | 1464 | 265 | 261 | 276 |
| 26 | 2021-2022 | 54156:54 | 54156 | 54 | 1425 | 314 | 314 | 332 |
| 27 | 2021-2022 | 55960:8 | 55960 | 8 | 1142 | 263 | 265 | 271 |
| 28 | 2021-2022 | 55981:8 | 55981 | 8 | 1037 | 218 | 216 | 219 |
| 29 | 2021-2022 | 57033:5 | 57033 | 5 | 1484 | 286 | 284 | 296 |
| 30 | 2021-2022 | 57125:7 | 57125 | 7 | 1438 | 271 | 271 | 282 |
| 31 | 2021-2022 | 57135:5 | 57135 | 5 | 1479 | 288 | 287 | 304 |
| 32 | 2021-2022 | 58119:20 | 58119 | 20 | 1208 | 271 | 271 | 281 |
| 33 | 2021-2022 | 58559:6 | 58559 | 6 | 1479 | 326 | 326 | 334 |
| 34 | 2021-2022 | 59957:32 | 59957 | 32 | 1409 | 299 | 296 | 315 |
| 35 | 2021-2022 | 62097:5 | 62097 | 5 | 1127 | 236 | 235 | 245 |
| 36 | 2021-2022 | 62159:17 | 62159 | 17 | 1220 | 266 | 267 | 283 |
| 37 | 2021-2022 | 62747:0 | 62747 | 0 | 1264 | 270 | 269 | 275 |
| 38 | 2021-2022 | 63626:11 | 63626 | 11 | 1169 | 236 | 236 | 244 |
| 39 | 2021-2022 | 64308:4 | 64308 | 4 | 1470 | 298 | 297 | 314 |
| 40 | 2021-2022 | 64395:2 | 64395 | 2 | 1488 | 295 | 293 | 305 |
| 41 | 2021-2022 | 64956:18 | 64956 | 18 | 1003 | 212 | 214 | 225 |
| 42 | 2021-2022 | 64991:0 | 64991 | 0 | 1304 | 272 | 272 | 280 |
| 43 | 2021-2022 | 65615:4 | 65615 | 4 | 1436 | 305 | 304 | 303 |
| 44 | 2021-2022 | 67080:13 | 67080 | 13 | 1022 | 202 | 196 | 207 |
| 45 | 2021-2022 | 67447:2 | 67447 | 2 | 1454 | 271 | 271 | 279 |
| 46 | 2021-2022 | 67854:9 | 67854 | 9 | 1140 | 236 | 235 | 241 |
| 47 | 2023-2024 | 26206:11 | 26206 | 11 | 1491 | 336 | 335 | 350 |
| 48 | 2023-2024 | 26703:57 | 26703 | 57 | 1349 | 278 | 277 | 291 |
| 49 | 2023-2024 | 27408:28 | 27408 | 28 | 1253 | 284 | 285 | 305 |
| 50 | 2023-2024 | 27831:4 | 27831 | 4 | 1450 | 315 | 315 | 330 |
| 51 | 2023-2024 | 28001:29 | 28001 | 29 | 1128 | 261 | 260 | 269 |
| 52 | 2023-2024 | 28222:42 | 28222 | 42 | 1022 | 235 | 234 | 252 |
| 53 | 2023-2024 | 29292:9 | 29292 | 9 | 1448 | 317 | 323 | 319 |
| 54 | 2023-2024 | 29524:2 | 29524 | 2 | 1364 | 278 | 277 | 297 |
| 55 | 2023-2024 | 31204:8 | 31204 | 8 | 942 | 184 | 186 | 193 |
| 56 | 2023-2024 | 31324:51 | 31324 | 51 | 1303 | 286 | 281 | 302 |
| 57 | 2023-2024 | 31342:7 | 31342 | 7 | 1356 | 264 | 259 | 274 |
| 58 | 2023-2024 | 31767:45 | 31767 | 45 | 1477 | 340 | 339 | 357 |
| 59 | 2023-2024 | 33202:38 | 33202 | 38 | 1224 | 275 | 274 | 284 |
| 60 | 2023-2024 | 33667:54 | 33667 | 54 | 1315 | 283 | 283 | 299 |
| 61 | 2023-2024 | 35226:4 | 35226 | 4 | 1148 | 293 | 292 | 290 |
| 62 | 2023-2024 | 38029:9 | 38029 | 9 | 1163 | 258 | 258 | 257 |
| 63 | 2023-2024 | 39657:12 | 39657 | 12 | 1413 | 300 | 306 | 312 |
| 64 | 2023-2024 | 39855:65 | 39855 | 65 | 1453 | 335 | 334 | 355 |
| 65 | 2023-2024 | 44003:48 | 44003 | 48 | 1041 | 213 | 213 | 236 |
| 66 | 2023-2024 | 44767:10 | 44767 | 10 | 1455 | 289 | 287 | 303 |
| 67 | 2023-2024 | 45094:16 | 45094 | 16 | 855 | 187 | 186 | 196 |
| 68 | 2023-2024 | 45842:43 | 45842 | 43 | 1210 | 256 | 256 | 272 |
| 69 | 2023-2024 | 46819:35 | 46819 | 35 | 1226 | 267 | 267 | 278 |
| 70 | 2023-2024 | 46979:7 | 46979 | 7 | 1253 | 265 | 262 | 275 |
| 71 | 2023-2024 | 47382:54 | 47382 | 54 | 1093 | 249 | 249 | 263 |
| 72 | 2023-2024 | 48967:64 | 48967 | 64 | 975 | 211 | 211 | 225 |
| 73 | 2023-2024 | 52314:1 | 52314 | 1 | 1427 | 292 | 291 | 299 |
| 74 | 2023-2024 | 52314:7 | 52314 | 7 | 864 | 182 | 181 | 182 |
| 75 | 2025-2026 | 189:9 | 189 | 9 | 1262 | 268 | 266 | 281 |
| 76 | 2025-2026 | 223:6 | 223 | 6 | 1464 | 310 | 311 | 321 |
| 77 | 2025-2026 | 413:6 | 413 | 6 | 1013 | 194 | 194 | 207 |
| 78 | 2025-2026 | 3692:8 | 3692 | 8 | 1311 | 289 | 288 | 303 |
| 79 | 2025-2026 | 4384:13 | 4384 | 13 | 1343 | 288 | 289 | 304 |
| 80 | 2025-2026 | 5339:14 | 5339 | 14 | 1465 | 332 | 329 | 346 |
| 81 | 2025-2026 | 6004:10 | 6004 | 10 | 933 | 202 | 202 | 211 |
| 82 | 2025-2026 | 7100:39 | 7100 | 39 | 1395 | 316 | 313 | 330 |
| 83 | 2025-2026 | 7100:58 | 7100 | 58 | 792 | 182 | 179 | 196 |
| 84 | 2025-2026 | 7746:9 | 7746 | 9 | 1140 | 254 | 254 | 271 |
| 85 | 2025-2026 | 8240:5 | 8240 | 5 | 1465 | 364 | 361 | 378 |
| 86 | 2025-2026 | 10375:8 | 10375 | 8 | 1178 | 274 | 275 | 278 |
| 87 | 2025-2026 | 10697:12 | 10697 | 12 | 1246 | 296 | 295 | 308 |
| 88 | 2025-2026 | 14049:5 | 14049 | 5 | 1078 | 236 | 236 | 236 |
| 89 | 2025-2026 | 15541:28 | 15541 | 28 | 1482 | 344 | 342 | 363 |
| 90 | 2025-2026 | 15998:43 | 15998 | 43 | 1091 | 255 | 255 | 272 |
| 91 | 2025-2026 | 16114:4 | 16114 | 4 | 1160 | 243 | 243 | 248 |
| 92 | 2025-2026 | 22575:22 | 22575 | 22 | 977 | 215 | 208 | 231 |
| 93 | 2025-2026 | 22575:37 | 22575 | 37 | 1300 | 269 | 266 | 277 |
| 94 | 2025-2026 | 23452:10 | 23452 | 10 | 1250 | 258 | 251 | 269 |
| 95 | 2025-2026 | 23699:3 | 23699 | 3 | 1354 | 300 | 286 | 324 |
| 96 | 2025-2026 | 23699:6 | 23699 | 6 | 1406 | 326 | 307 | 353 |
| 97 | 2025-2026 | 24684:61 | 24684 | 61 | 1126 | 258 | 246 | 275 |
| 98 | 2025-2026 | 120669:5 | 120669 | 5 | 1176 | 244 | 244 | 253 |
| 99 | 2025-2026 | 121360:0 | 121360 | 0 | 1426 | 296 | 294 | 299 |
| 100 | 2025-2026 | 123037:9 | 123037 | 9 | 1010 | 200 | 200 | 204 |
