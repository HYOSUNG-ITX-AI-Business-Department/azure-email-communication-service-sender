# OpenSSF Best Practices Badge (CII-Best-Practices)

## Goal

OpenSSF Scorecard's **CII-Best-Practices** signal is 0 when a repository is not
enrolled in the **OpenSSF Best Practices Badge** program.

This repository tracks the enrollment and required follow-ups in the related
issue.

## Where to enroll

Create a project on bestpractices.dev:

- [https://www.bestpractices.dev/en/projects/new](https://www.bestpractices.dev/en/projects/new)

Once created, the project will have a numeric project ID (needed for the badge
link).

## Suggested workflow

1) Enroll the repository and connect it to GitHub.
2) Target at least the **Passing** level.
3) After the project is created, add the badge to `README.md`.
   Do **not** commit the placeholder `<PROJECT_ID>`; replace it with the actual
   numeric project ID first.

```markdown
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/<PROJECT_ID>/badge)](
  https://www.bestpractices.dev/projects/<PROJECT_ID>
)
```

## Repository checklist (common items)

This section maps common repo items to the OpenSSF Best Practices levels.

Sources:

- Passing criteria: <https://www.bestpractices.dev/en/criteria/0>
- All levels: <https://www.bestpractices.dev/en/criteria>

### Passing (MUST / required)

- ✅ LICENSE is published in the repo (`LICENSE`) (license location MUST)
- ✅ Vulnerability reporting process is published (`SECURITY.md`) (MUST)
- ✅ At least one automated test suite exists and is documented (MUST)

### Passing (SUGGESTED / recommended)

- ✅ Continuous integration runs automated tests (SUGGESTED)

### Silver / Gold (higher levels)

- ✅ Dependency monitoring / update process (e.g., Dependabot) (Silver MUST)
- ✅ Code review by someone other than the author (Gold MUST)

Potential follow-ups (if required by the badge questionnaire):

- CONTRIBUTING.md and governance/process documentation
- Code of Conduct
- Release/versioning policy
- Vulnerability disclosure expectations (timeline, contact channels)

## Notes

- Enrollment and most questionnaire steps are **manual actions outside git**.
- Avoid committing any secrets/tokens while integrating badges or links.
