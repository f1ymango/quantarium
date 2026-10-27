"""Quantarium's security suite: misuse tests against disposable workspaces (docs/plans/v0.5.x-security-hardening.md).

Every test here follows the plan's rules of engagement:

- it targets Quantarium's own code, on loopback, in a temporary workspace;
- it plants fake canary secrets and checks they never leak;
- it contacts no network beyond loopback;
- it checks that nothing outside its temporary folders changes.

Tests check that Quantarium refuses misuse. They do not look for ways around
Python's or the operating system's protections.
"""
