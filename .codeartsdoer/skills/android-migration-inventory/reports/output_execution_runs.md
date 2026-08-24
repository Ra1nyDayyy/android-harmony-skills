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
| inventory-file-backed | baseline | recorded_fixture |  |  | 20 | 0.0 | pass |
| inventory-file-backed | with_skill | recorded_fixture |  |  | 65 | 100.0 | pass |
| inventory-static-runtime | baseline | recorded_fixture |  |  | 20 | 0.0 | pass |
| inventory-static-runtime | with_skill | recorded_fixture |  |  | 65 | 100.0 | pass |
| inventory-model-pass | baseline | recorded_fixture |  |  | 29 | 0.0 | pass |
| inventory-model-pass | with_skill | recorded_fixture |  |  | 63 | 100.0 | pass |
| inventory-near-neighbor | baseline | recorded_fixture |  |  | 26 | 0.0 | pass |
| inventory-near-neighbor | with_skill | recorded_fixture |  |  | 54 | 100.0 | pass |
| inventory-unreachable | baseline | recorded_fixture |  |  | 30 | 0.0 | pass |
| inventory-unreachable | with_skill | recorded_fixture |  |  | 69 | 100.0 | pass |

## Next Fixes

- Keep recorded fixtures as reproducible baselines, but do not describe them as model-executed evidence.
- Use `scripts/provider_output_eval_runner.py` for provider-backed holdout cases when release confidence depends on real generation behavior.
- Compare timing, token cost, and assertion deltas before promoting a skill to governed reuse.
