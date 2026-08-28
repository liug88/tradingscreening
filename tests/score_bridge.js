/* Runs site/score.js the way the browser runs it, for the parity test.
 *
 * The page loads score.js with a plain <script> tag: no modules, no exports,
 * it just hangs Score off window. So this loads it the same way -- an indirect
 * eval against a stubbed window -- rather than refactoring the file into
 * something importable. What the test checks is what the browser executes.
 *
 * Reads one job as JSON on stdin, writes the answers as JSON on stdout.
 */

const fs = require("fs");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "site", "score.js"), "utf8");

globalThis.window = {};
(0, eval)(source);
const Score = globalThis.window.Score;

const job = JSON.parse(fs.readFileSync(0, "utf8"));
const answer = {};

if (job.rows) {
  /* Defaults to the put, which is what every job asked for before there were
     three rankings and what the frozen fixture is scored under. */
  const profile = job.profile || "put";
  answer.scored = job.rows.map((row) => {
    const out = Score.rescore(row, job.config, profile);
    return {
      symbol: row.symbol,
      score: out.score,
      components: out.components,
      penalties: out.penalties,
    };
  });
  answer.order = Score.rescoreAll(job.rows, job.config, profile).map((r) => r.symbol);
}

if (job.penalty_rows) {
  /* The merged block, not the shared one: a ranking can charge more for the
     same fault. With no overrides published for a profile the merge is the
     shared block unchanged, which is what every job asked for before. */
  const pp = job.penalty_profile || "put";
  answer.penalties = job.penalty_rows.map(
    (row) => Score.penalties(row, Score.penaltyConfig(job.config, pp), pp));
}

process.stdout.write(JSON.stringify(answer));
