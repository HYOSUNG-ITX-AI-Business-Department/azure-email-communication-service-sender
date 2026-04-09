# OpenSSF Best Practices Badge Enrollment Guide

## Overview

This document provides a step-by-step guide for enrolling this repository in the [OpenSSF Best Practices Badge Program](https://www.bestpractices.dev/) and achieving at least "Passing" level certification.

The OpenSSF (Open Source Security Foundation) Best Practices Badge demonstrates that the project follows security best practices and is a key metric in supply chain security scorecards.

## Why Enroll?

- **Supply Chain Security**: Improves the repository's Scorecard `CII-Best-Practices` score
- **Security Posture**: Validates adherence to security best practices
- **Trust & Transparency**: Demonstrates commitment to secure development
- **Community Standards**: Shows alignment with open source security standards

## Prerequisites

Before enrolling, ensure the following are in place:

- [ ] Repository has a clear open source license (✓ MIT License exists)
- [ ] SECURITY.md with vulnerability reporting process (✓ exists)
- [ ] README.md with project description and usage instructions (✓ exists)
- [ ] Some form of version control and change documentation
- [ ] Active maintenance and development

## Enrollment Steps

### 1. Create Account on Best Practices Site

1. Visit: https://www.bestpractices.dev/en/projects/new
2. Sign in using your GitHub account
3. Authorize the OpenSSF Best Practices Badge application

### 2. Register This Repository

1. On the "New Project" page, enter:
   - **Project Name**: `azure-email-communication-service-sender`
   - **Project URL**: `https://github.com/HYOSUNG-ITX-AI-Business-Department/azure-email-communication-service-sender`
   - **Repository URL**: `https://github.com/HYOSUNG-ITX-AI-Business-Department/azure-email-communication-service-sender`
   - **Description**: Brief description from README
   - **License**: MIT License

2. Submit the registration

3. You will receive a project ID (used for badge embedding)

### 3. Complete the Badge Questionnaire

The questionnaire covers several categories. Below is a checklist mapped to this repository's current state:

#### Basics

- [ ] **Website**: Project URL points to GitHub repo
- [ ] **Description**: Clear description in README
- [ ] **License**: Open source license file present (MIT)
- [ ] **Project Activity**: Regular commits and maintenance

#### Change Control

- [x] **Public Version Control**: Uses GitHub
- [x] **Unique Version Numbering**: Uses git tags/commits
- [ ] **Release Notes**: Document changes in releases
  - *Action*: Ensure releases have descriptive notes

#### Reporting

- [x] **Bug Reporting**: GitHub Issues enabled
- [x] **Vulnerability Reporting**: SECURITY.md exists with reporting process
- [ ] **Security Response**: Documented response time/process
  - *Current*: SECURITY.md mentions GitHub Private Vulnerability Reporting

#### Quality

- [ ] **Working Build System**: Documented in README
  - *Status*: Docker and local setup documented
- [ ] **Automated Test Suite**: Tests exist and can be run
  - *Check*: Verify tests/ directory has functional tests
- [ ] **Continuous Integration**: Automated testing on commits
  - *Status*: CI workflows exist (.github/workflows/ci.yml)

#### Security

- [x] **Secure Delivery**: Uses HTTPS (GitHub)
- [x] **Static Analysis**: CodeQL enabled (.github/workflows/codeql.yml)
- [x] **Dependency Monitoring**: Dependabot enabled (.github/dependabot.yml)
- [ ] **No Hardcoded Secrets**: Code review confirms
  - *Action*: Verify no secrets in codebase
- [ ] **Security Testing**: Regular security scans
  - *Status*: Scorecard and CodeQL workflows active

#### Analysis

- [ ] **Dynamic Analysis**: If applicable
- [ ] **Static Code Analysis**: CodeQL performs this
- [ ] **Memory Safety**: Use of safe languages (Python)

### 4. Review and Submit

1. Complete all applicable criteria in the questionnaire
2. Provide evidence/links where requested
3. Submit for review
4. Address any feedback from OpenSSF reviewers

### 5. Achieve "Passing" Level

- Work through any incomplete criteria
- Provide justification or N/A for non-applicable items
- Re-submit until "Passing" badge is achieved

## Badge Integration

Once the project achieves "Passing" status, add the badge to the README:

### Badge Markdown

```markdown
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/YOUR_PROJECT_ID/badge)](https://www.bestpractices.dev/projects/YOUR_PROJECT_ID)
```

Replace `YOUR_PROJECT_ID` with the actual project ID from bestpractices.dev.

### Suggested Badge Placement

Add the badge to the top of `README.md`, in a "Badges" section below the title:

```markdown
# Azure Email Communication Service Sender

[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/YOUR_PROJECT_ID/badge)](https://www.bestpractices.dev/projects/YOUR_PROJECT_ID)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/HYOSUNG-ITX-AI-Business-Department/azure-email-communication-service-sender/badge)](https://scorecard.dev/viewer/?uri=github.com/HYOSUNG-ITX-AI-Business-Department/azure-email-communication-service-sender)

REST API service for sending emails via Azure Communication Services (ACS) Email SMTP Relay.
```

## Verification Checklist

Use this checklist to verify readiness for each major criterion:

### Documentation

- [x] Project has README with description
- [x] Project has LICENSE file
- [x] Project has SECURITY.md
- [ ] Project has CONTRIBUTING guidelines (optional but recommended)
- [ ] Project has CODE_OF_CONDUCT (optional but recommended)

### Security

- [x] Vulnerability reporting process documented
- [x] Dependency scanning enabled (Dependabot)
- [x] Static analysis enabled (CodeQL)
- [x] Security policy workflow (Scorecard)
- [ ] No secrets in repository
- [ ] Secure default configuration
- [ ] Input validation in code

### Development

- [x] Version control (Git/GitHub)
- [x] Public issue tracker
- [ ] Automated tests exist and pass
- [ ] Build/deployment documented
- [ ] CI/CD pipeline functional

### Maintenance

- [x] Project is actively maintained
- [ ] Issues are responded to
- [ ] Pull requests are reviewed
- [ ] Regular releases or updates

## Maintenance and Renewal

- **Annual Review**: Best Practices Badge requires annual re-verification
- **Update Criteria**: As project evolves, update questionnaire responses
- **Monitor Badge Status**: Check badge page for expiration warnings
- **Continuous Improvement**: Use badge criteria as security improvement roadmap

## Expected Impact on Scorecard

After achieving "Passing" badge and completing the next Scorecard run:

- `CII-Best-Practices` score will increase from 0 to maximum points
- Overall Scorecard score will improve
- Supply chain security posture is strengthened

## References

- OpenSSF Best Practices Badge: https://www.bestpractices.dev/
- Badge Criteria: https://www.bestpractices.dev/en/criteria
- OpenSSF Scorecard: https://github.com/ossf/scorecard
- Badge Application: https://www.bestpractices.dev/en/projects/new

## Next Steps

1. **Enroll** the project at https://www.bestpractices.dev/en/projects/new
2. **Complete** the questionnaire with current project status
3. **Achieve** at least "Passing" level
4. **Update** README.md with badge once enrolled
5. **Monitor** badge status annually and keep criteria current

## Support

For questions about the enrollment process:
- OpenSSF Best Practices Badge Documentation: https://github.com/coreinfrastructure/best-practices-badge/blob/main/doc/
- OpenSSF Community: https://openssf.org/community/

---

**Note**: This enrollment requires manual steps on bestpractices.dev. This document serves as a runbook for maintainers to follow. Track progress in the related GitHub issue.
