# Output Risk Profile

Skill: `harmonyos-feature-implementation`

## Why This Exists

Generated skills often fail in small output details: generic headings, cluttered citations, fragile screenshots, weak Markdown rendering, or missing execution assumptions. This profile predicts the most likely output mistakes before the skill is used heavily.

## Matched Risk Families

### Screenshot and visual capture
- Matched keywords: screenshot, image, visual, screen, capture
- Score: `5`

### Markdown readability
- Matched keywords: md, table, report
- Score: `3`

### Code and command safety
- Matched keywords: code, script, command
- Score: `3`

### Citation and footnote clutter
- Matched keywords: source, reference
- Score: `2`

### Tone and specificity
- Matched keywords: copy
- Score: `1`

## Likely Output Mistakes

- Screenshots can be captured from the wrong state, wrong viewport, or wrong crop.
- Missing screenshots can cause the skill to invent visual references instead of declaring the gap.
- Tables can render as dense grids with weak hierarchy or poor mobile readability.
- Long bullets can make the output look complete while hiding the actual decision logic.
- Commands can omit environment assumptions, working directory, or rollback notes.
- Code snippets can look runnable while missing required inputs.

## Output Constraints To Apply

- Never invent a screenshot; state when visual evidence is missing.
- Record the source, viewport, and crop intent for any screenshot-dependent output.
- Use tables only when comparison is the main job; otherwise prefer compact cards or grouped bullets.
- Keep table cells short and move explanations below the table.
- Name the working directory, required inputs, and expected output for each command.
- Mark destructive or external side-effect operations explicitly.

## Self-Repair Checks

- Check that every screenshot reference points to a real provided or generated asset.
- Reword any visual instruction that depends on an unseen screen state.
- Preview whether each table still reads well when columns are narrow.
- Convert any table with paragraph-length cells into bullets or cards.
- Scan each command for cwd, input, output, and side-effect assumptions.
- Remove speculative error handling that is not tied to a real failure mode.

## Reviewer Note

Use this report before deepening the package and again before approving example outputs.
