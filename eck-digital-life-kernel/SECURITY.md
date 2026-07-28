# Security Policy

## Supported version

Only the latest `0.1.x` release is supported during the alpha phase.

## Reporting

Do not publish exploitable details in a public issue. Use the repository
security-advisory channel after the GitHub repository is created.

## v0.1 threat boundary

ECK v0.1 is a local research runtime, not a hardened multi-tenant service.
It must not be exposed directly to the public internet.

Default controls:

- localhost-only publishing;
- no arbitrary shell capability;
- no arbitrary generated Python execution;
- system-file mutation disabled;
- network capabilities disabled;
- high-risk actions require approval;
- Docker root filesystem read-only;
- all Linux capabilities dropped;
- `no-new-privileges` enabled.

These controls do not make the process safe for hostile code. Generated code is
restricted to the arithmetic-expression capability with an AST allowlist.

