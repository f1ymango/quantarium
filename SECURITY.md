# Security

Quantarium runs on your own computer. Its server listens only on a loopback address, it never places real orders, and its agent features are off until you turn them on. The design and its limits are described in the README's Security section and in the [security reviews](docs/).

## Reporting a vulnerability

Please report vulnerabilities privately, through GitHub's **Report a vulnerability** button on this repository's Security tab. Do not open a public issue, and do not include real API keys, tokens, or account details in a report: a description and the steps to reproduce it are enough.

You can expect an acknowledgement within a week. Fixes are released as a new version with a note in the changelog; reporters are credited unless they prefer not to be.

## What counts

Reports are especially welcome when they show any of the following:

- an AI agent, through its token or the MCP tools, doing something its permissions do not allow: reading hidden folders or holdout scenarios, running a decider, writing outside the workspace, or approving its own proposals;
- agent-written code running outside the sandbox, or reaching the network, files, or secrets from inside it;
- an API key or token written to disk, a log, or a response, or sent anywhere other than its connection's endpoint;
- a web page or other local program changing Quantarium's state without the browser token;
- a crafted file (a scenario, data folder, decision file, trades file, or evidence report) that makes Quantarium crash, hang, or execute code instead of refusing it.

Out of scope: problems that need an attacker to already control your user account or Python environment, results of strategies you run yourself (they are trusted local Python), and the accuracy of market data you bring.

## Supported versions

Only the latest release receives security fixes.
