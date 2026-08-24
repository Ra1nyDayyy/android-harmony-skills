# Output Execution Runs

This report records how output-eval variants were produced and whether timing or token evidence is observed or estimated.

- Cases: `5`
- Variant runs: `10`
- Command executed: `0`
- Model executed: `0`
- Recorded fixtures: `10`
- Timing observed: `0`
- Token observed: `0`
- Token estimated: `10`
- Delta: `100.0`
- Gate pass: `True`

No model-executed runs are recorded yet.

Use `python3 scripts/yao.py output-exec --provider-runner openai --self` or `--runner-command` with a reviewed provider-backed runner to replace recorded fixtures with real model output evidence.

## Runs

| Case | Variant | Mode | Model | Duration ms | Tokens | Score | Status |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| scaffold-file-backed | baseline | recorded_fixture |  |  | 31 | 0.0 | pass |
| scaffold-file-backed | with_skill | recorded_fixture |  |  | 79 | 100.0 | pass |
| scaffold-no-business | baseline | recorded_fixture |  |  | 30 | 0.0 | pass |
| scaffold-no-business | with_skill | recorded_fixture |  |  | 65 | 100.0 | pass |
| scaffold-real-cli | baseline | recorded_fixture |  |  | 22 | 0.0 | pass |
| scaffold-real-cli | with_skill | recorded_fixture |  |  | 75 | 100.0 | pass |
| scaffold-near-neighbor | baseline | recorded_fixture |  |  | 26 | 0.0 | pass |
| scaffold-near-neighbor | with_skill | recorded_fixture |  |  | 64 | 100.0 | pass |
| scaffold-claim-boundary | baseline | recorded_fixture |  |  | 25 | 0.0 | pass |
| scaffold-claim-boundary | with_skill | recorded_fixture |  |  | 58 | 100.0 | pass |

## Next Fixes

- Keep recorded fixtures as reproducible baselines, but do not describe them as model-executed evidence.
- Use `scripts/provider_output_eval_runner.py` for provider-backed holdout cases when release confidence depends on real generation behavior.
- Compare timing, token cost, and assertion deltas before promoting a skill to governed reuse.
