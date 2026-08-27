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
  answer.scored = job.rows.map((row) => {
    const out = Score.rescore(row, job.config);
    return {
      symbol: row.symbol,
      score: out.score,
      components: out.components,
      penalties: out.penalties,
    };
  });
  answer.order = Score.rescoreAll(job.rows, job.config).map((r) => r.symbol);
}

if (job.penalty_rows) {
  answer.penalties = job.penalty_rows.map(
    (row) => Score.penalties(row, job.config.penalties));
}

process.stdout.write(JSON.stringify(answer));
