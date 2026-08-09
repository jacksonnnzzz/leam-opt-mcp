# Security policy

## Reporting

Please report vulnerabilities through GitHub private vulnerability reporting once the
repository is published. Do not include active API keys, license files, proprietary paper
attachments, or private HFSS projects in a public issue.

If a credential was pasted into a terminal log, screenshot, issue, commit, or chat, revoke it
at the provider and create a replacement. Removing it from the latest commit is not sufficient
after it has been exposed.

## Execution boundary

Generated Python is untrusted engineering output. The project performs AST checks and uses
content-addressed approval hashes, but it is not a general-purpose sandbox. Review generated
code before passing an HFSS object to `build(hfss)`.

HFSS build, solve, and optimization are disabled by default. Only enable
`ANTENNA_MCP_ALLOW_SIMULATION=1` after reviewing the target project, generated artifacts,
ports, boundaries, mesh, sweep, parameter ranges, and expected compute/license usage.

The repository does not provide or modify Ansys licenses. Use only a valid official license
and follow the license terms for every machine that runs AEDT.
