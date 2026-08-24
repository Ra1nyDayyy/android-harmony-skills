# Output Risk Profile

Skill: `harmonyos-migration-scaffold`

## Why This Exists

Generated skills often fail in small output details: generic headings, cluttered citations, fragile screenshots, weak Markdown rendering, or missing execution assumptions. This profile predicts the most likely output mistakes before the skill is used heavily.

## Matched Risk Families

### Markdown readability
- Matched keywords: md, table, report, doc
- Score: `4`

### Screenshot and visual capture
- Matched keywords: screenshot, visual, screen, capture
- Score: `4`

### Code and command safety
- Matched keywords: code, script, command
- Score: `3`

### Citation and footnote clutter
- Matched keywords: source, reference
- Score: `2`

### Tone and specificity
- Matched keywords: copy, content
- Score: `2`

## Likely Output Mistakes

- Tables can render as dense grids with weak hierarchy or poor mobile readability.
- Long bullets can make the output look complete while hiding the actual decision logic.
- Screenshots can be captured from the wrong state, wrong viewport, or wrong crop.
- Missing screenshots can cause the skill to invent visual references instead of declaring the gap.
- Commands can omit environment assumptions, working directory, or rollback notes.
- Code snippets can look runnable while missing required inputs.

## Output Constraints To Apply

- Use tables only when comparison is the main job; otherwise prefer compact cards or grouped bullets.
- Keep table cells short and move explanations below the table.
- Never invent a screenshot; state when visual evidence is missing.
- Record the source, viewport, and crop intent for any screenshot-dependent output.
- Name the working directory, required inputs, and expected output for each command.
- Mark destructive or external side-effect operations explicitly.

## Self-Repair Checks

- Preview whether each table still reads well when columns are narrow.
- Convert any table with paragraph-length cells into bullets or cards.
- Check that every screenshot reference points to a real provided or generated asset.
- Reword any visual instruction that depends on an unseen screen state.
- Scan each command for cwd, input, output, and side-effect assumptions.
- Remove speculative error handling that is not tied to a real failure mode.

## Reviewer Note

Use this report before deepening the package and again before approving example outputs.
