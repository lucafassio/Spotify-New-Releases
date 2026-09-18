# Domain Docs

How the engineering skills should consume this repo's domain documentation.

## Before exploring, read these

- **`CONTEXT.md`** — glossary / ubiquitous language for the project.
- **`docs/adr/`** — read ADRs that touch the area you're about to work in.

If any of these files don't exist, **proceed silently**. The `/domain-modeling` skill creates them
lazily when terms or decisions actually get resolved.

## File structure

Single-context repo:

```
/
├── CONTEXT.md
├── docs/
│   ├── adr/
│   │   └── NNNN-short-slug.md
│   └── agents/
│       ├── issue-tracker.md
│       └── domain.md
└── src/
```

## Use the glossary's vocabulary

When your output names a domain concept, use the term as defined in `CONTEXT.md`. Don't drift to
synonyms the glossary explicitly avoids. Missing concept = signal for `/domain-modeling`.

## Writing conventions

- **No accents** anywhere: glossary, ADRs, comments, commit messages.
- ADRs are numbered `NNNN-short-slug.md` under `docs/adr/`.

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding.
