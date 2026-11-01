# Show IAM conditions on resource bindings

**Summary:** `iam_bindings` on a resource (`--include-iam`, with or without `--effective`) drops
each binding's IAM condition, so a conditional grant reads as unconditional. `Grant` rows
already carry `condition`. Resource bindings should too.

**Branch context:** noticed while building `--effective` on the `effective-iam` branch. Left out
of that change because adding the field to attached bindings changes default output, and that
PR promised it would not. Branch fresh from `main`.

## Context

- `app/models.py`: `IamBinding` has `role` and `members` only.
- `app/aggregate.py`: `fetch_iam_bindings` reads `binding.condition.expression` nowhere.
- `app/effective.py`: `_inherited` builds inherited bindings the same way.
- Use `Field(exclude_if=lambda v: v is None)`, as `inherited_from` does, so an unconditional
  binding's JSON is unchanged. Only conditional bindings gain a key.

## Why it matters

The error points in the safe direction, since it shows more access than exists. But a reviewer
who revokes a grant that was already limited to a time window wastes effort, and one who trusts
the tool's output loses the information.

## Acceptance criteria

- [ ] A conditional binding shows `condition` on both the attached and the inherited path
- [ ] An unconditional binding's JSON is byte-identical to today
- [ ] `scripts/compare-effective-iam.sh` (or a sibling) compares conditions too
