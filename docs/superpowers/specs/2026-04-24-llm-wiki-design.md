---
title: LLM Wiki for Lethe
date: 2026-04-24
status: approved
---

# LLM Wiki for Lethe — Design

## Goal

Maintain a local `wiki/` directory of structured markdown files that give an LLM long-running, compounding context about the Lethe codebase. Primary consumer is the LLM (Claude/Gemini); human-readable as a side effect. Inspired by Karpathy's LLM-wiki pattern.

## Directory Structure

```
wiki/
  index.md        # catalog — one-line summary per page, by category
  log.md          # append-only chronological record of all wiki updates
  architecture.md # system overview, component layout, data flow, tech stack
  api.md          # all endpoints: method, path, fields, response shape
  data-model.md   # Firestore schema, node/edge types, field definitions
  algorithms.md   # collision detection, temporal decay, BFS pruning, consolidation
  decisions.md    # ADRs: key architectural decisions and rationale
```

## Content Philosophy

- Dense and structured — short headers, bullet facts, exact field names and types
- No prose paragraphs; written so an LLM can read a page and immediately understand the area without reading source code
- Each page is self-contained (no required reading order)

## Schema (Maintenance Rules — encoded in CLAUDE.md)

1. **Session start**: Read `wiki/index.md` for orientation before any non-trivial code work
2. **Targeted reads**: Pull specific pages when working in that area (e.g., touching `lethe/graph/` → read `algorithms.md`)
3. **Update on change**: After any significant code change, update the relevant page(s) and append one line to `log.md`
4. **Lint periodically**: Verify `index.md` summaries match page content; check no page is orphaned; flag contradictions

## `log.md` Format

```
YYYY-MM-DD: [page] description of change
```

Example:
```
2026-04-24: [architecture] Added uvloop event loop detail
2026-04-24: [algorithms] Documented self_seed_neighbor_floor pruning guarantee
```

## Initial Page Content Sources

| Page | Source |
|------|--------|
| architecture.md | `lethe/main.py`, `lethe/config.py`, `README.md`, `Dockerfile` |
| api.md | `lethe/routers/`, `README.md` |
| data-model.md | `specs/001-knowledge-graph-spec/data-model.md`, `lethe/models/` |
| algorithms.md | `lethe/graph/`, `lethe/prompts/`, spec.md algorithm sections |
| decisions.md | `specs/001-knowledge-graph-spec/spec.md` assumptions + git log |

## LLM Instruction File Changes

Add a `## Wiki` section to both `CLAUDE.md` and `GEMINI.md` with:
- When to read wiki pages
- How to update pages and log.md
- Lint checklist

## Out of Scope

- Automated wiki updates (no scripts or CI hooks — LLM updates manually per session)
- Query tooling (no search scripts — LLM reads directly)
- Wiki versioning beyond git history
