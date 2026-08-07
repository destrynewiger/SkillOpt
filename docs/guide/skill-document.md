# Skill Document

A **skill document** is a Markdown file that serves as the "prompt weights" of your agent. SkillOpt trains this document through iterative optimization.

## What is a Skill Document?

A skill document is a structured set of instructions that tells a language model **how** to approach a specific type of task. It's analogous to learned weights in a neural network — encoding task-specific knowledge in natural language rather than floating-point parameters.

## Structure

A typical skill document contains:

```markdown
# Task Strategy

## General Approach
- Break complex problems into sub-steps
- Always verify intermediate results

## Common Patterns
- When you see X, try approach Y
- Avoid Z because it leads to errors

## Edge Cases
- If the input contains A, handle it specially by...
- Watch out for B — it requires C

## Output Format
- Always include reasoning before the answer
- Format numbers with proper units
```

## How It Evolves

During training, the skill document is modified by **edit patches**:

1. **Additions**: New rules or strategies discovered from failed trajectories
2. **Modifications**: Refining existing rules that are partially correct
3. **Deletions**: Removing rules that consistently lead to errors

Selected edits are applied together to produce a candidate skill. With the
validation gate enabled, that candidate replaces the current skill only when
its score on the selection split strictly improves.

SkillOpt may maintain two protected, machine-managed regions:

```markdown
<!-- SLOW_UPDATE_START -->
... epoch-level longitudinal guidance ...
<!-- SLOW_UPDATE_END -->

<!-- APPENDIX_START -->
... skill-aware execution reminders ...
<!-- APPENDIX_END -->
```

Normal edit patches cannot modify either region. Slow update owns the first;
optional skill-aware reflection owns the second. Preserve these markers when
copying or manually inspecting a trained skill.

## Initial Skill

You can start training with:

- **Empty skill**: Point `env.skill_init` to an empty Markdown file
- **Seed skill**: Provide initial instructions to bootstrap training
- **Pre-trained skill**: Transfer a skill from a related benchmark

Configure the initial skill in your YAML:

```yaml
env:
  skill_init: path/to/initial_skill.md
```

To start from scratch, create an empty Markdown file and use its path. A missing
path currently also starts blank, so using an explicit file avoids silently
treating a typo as an empty skill.

## Skill Quality Metrics

Track your skill's evolution through:

- **Validation score**: Primary metric on the selection split
- **Test score**: Final metric on held-out test data
- **Skill length**: Total tokens in the document
- **Candidate acceptance rate**: Fraction of candidate skill updates that pass
  gating; multiple proposed edits can be combined into one candidate

## Best Practices

!!! tip "Tips for better skills"
    1. **Start with a seed skill** (`env.skill_init`) if you have domain knowledge — it converges faster
    2. **Use cosine LR schedule** — aggressive early exploration + careful late refinement
    3. **Enable slow update** (`optimizer.use_slow_update: true`) to counter forgetting across epochs
    4. **Enable meta skill** (`optimizer.use_meta_skill: true`) so the optimizer accumulates strategy memory

## Next Steps

- [Deep Learning Analogy](dl-analogy.md)
- [Configuration Reference](../reference/config.md)
