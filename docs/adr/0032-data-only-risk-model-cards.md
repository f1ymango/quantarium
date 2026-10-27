# ADR-0032: Data-only risk model cards and bound training labels

- **Status:** Accepted for v0.4.2
- **Date:** 2026-10-22
- **Deciders:** Barloom maintainers
- **Review:** ChatGPT-6 Sol

## Context

The Lab previously recorded a cell's `information_end` date as a user declaration. It could not tie that date to a training run, and a model package should not add heavy dependencies to normal simulation or execute pickled objects when loaded. Kandodaki's research branch provides useful feature and risk-baseline ideas, while its research tooling remains a separate contribution that needs review.

## Decision

- Train an initial daily drop-risk baseline from Static-format CSV in a separate child process. The label observes the lowest low over a fixed number of completed sessions after the next open. A row is admitted only if its *label end* is no later than `--through`. Features use completed bars through the decision close and require uninterrupted market sessions.
- Store the resulting feature recipe, label recipe, numeric weights, training algorithm, input SHA-256, source declaration, and final admitted label date in a JSON card. Its filename contains a digest of its exact bytes. The simulation uses standard-library inference and refuses a changed card. The optional `[ml]` extra provides NumPy and scikit-learn training; a deterministic standard-library trainer covers small local studies.
- Attach a card to a cell independently of its Simple or Complex mode. The engine computes estimates at a daily close, passes them as read-only `context.risk_estimates`, and records them in the journal. A card does not generate orders. Scripts that act on risk remain subject to a separately registered Lab comparison.
- Bind the cell's `information_end` to the card's last training label. Lab registration pins the card SHA-256. A whole-run Lab verdict is `unknown` if any scenario starts on or before that boundary; Barloom does not claim that a partial post-boundary segment passes a whole-run rule.
- Keep the data manifest's provider/source status `user_declared`. A hash verifies the input bytes the trainer saw, not their origin or point-in-time completeness. Lab caveats explicitly retain that limitation.

## Alternatives considered

- **Merge the entire research package now.** That would couple runtime, download, and exploratory analysis code before the cell contract is stable. The branch remains available for a scoped pull request and review.
- **Load a scikit-learn pickle in the simulator.** Pickles can execute code when loaded and require training dependencies at runtime. JSON numbers suffice for the initial linear model.
- **Trust a typed cutoff.** It would not move forward on retraining and could overlook labels that end after the last feature date. A card binds the effective cutoff to a saved training record.
- **Automatically trim holdings when risk rises.** Existing research found no supported improvement for tested risk actions. Estimates enter the context and journal first; any trading rule needs a registered comparison against simple controls.

## Consequences and limits

Content addressing detects a changed card, and the label boundary prevents a clean Lab verdict on overlapping history. It does not prove that the CSV really came from its named provider, that all previous model attempts were recorded, or that a user script did not read other information. The first model is a baseline with two price features, not a claim of predictive utility or calibration. The fallback trainer is slower on large CSVs. Generated cell configs contain a local absolute card path, so moving a workspace requires regenerating those configs from its cell library.

Daily model cards are refused for Live Feed minute bars. A minute strategy needs a separately specified feature and timing recipe rather than silently reusing daily estimates.
