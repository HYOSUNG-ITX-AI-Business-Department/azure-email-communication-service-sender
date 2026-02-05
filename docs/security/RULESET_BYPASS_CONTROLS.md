# Ruleset bypass actors & compensating controls

## Background

OpenSSF Scorecard's **Branch-Protection** signal can be reduced if branch
protection does **not** apply to administrators, or if a higher-level
organization ruleset includes **bypass actors**.

This repository currently has:

- **Repository ruleset** (no bypass actors) that enforces:
  - PR-only changes
  - 2 approving reviews
  - required status checks (test / dependency-review / deny-debug-true / analyze)
  - required resolution of review threads
- **Organization ruleset** that may include bypass actors for operational
  reasons (e.g., repository migration, deploy keys, integrations).

Scorecard may interpret the presence of bypass actors as "not maximally
protected" even if repository-level controls are strong.

## Why this matters

Bypass actors can allow changes to protected branches without going through the
full PR review + required checks gate. Some bypasses are necessary for
operations; others are optional.

The goal is to either:

1) Remove/limit bypass actors when they are not required, **or**
2) Document a justified exception and the **compensating controls** that keep
   risk acceptable.

## Compensating controls (current)

The following controls remain in place at the repository level:

- **Require PR** for changes to protected branches
- **Require 2 approvals**
- **Require required status checks** (strict)
- **Code scanning and dependency review** workflows are enabled
- **Runner hardening** is enabled in CI workflows (ci.yml, codeql.yml,
  dependency-review.yml) via the step-security/harden-runner action, which
  monitors and restricts network egress to prevent unauthorized data exfiltration

These controls mitigate the risk for day-to-day development changes.

## Operational exception guidance

If the organization must keep bypass actors enabled (e.g., for migrations or
critical automation), the expectation is:

- Limit bypass actors to the smallest necessary set
- Limit the bypass scope to only the branches that require it
- Maintain an audit trail of bypass usage
- Ensure compensating controls above remain enforced for normal contributors

## Evidence & verification commands

Use the GitHub CLI to review effective branch policy:

```bash
OWNER_REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
OWNER="${OWNER_REPO%%/*}"; REPO="${OWNER_REPO#*/}"
BASE_BRANCH="develop"

# Rulesets applied to BASE_BRANCH
gh api "repos/$OWNER/$REPO/rules/branches/$BASE_BRANCH"

# (Fallback) legacy branch protection (may be absent when rulesets are used)
gh api "repos/$OWNER/$REPO/branches/$BASE_BRANCH/protection" || true
```

## How to resolve the Scorecard alert

### Option A (preferred): remove/limit bypass actors

Update the **organization ruleset** to remove or constrain bypass actors for
the repository's default branch.

### Option B: dismiss with documented justification

If bypass actors are required and cannot be removed, dismiss the Scorecard alert
with a justification referencing:

- why the bypass actors are required
- which actors remain enabled
- the compensating controls listed in this document

## Related

- Issue: Scorecard BranchProtectionID bypass actors discussion
- GitHub Actions required status checks are enforced via repository rulesets
