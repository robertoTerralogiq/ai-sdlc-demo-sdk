# Acme Finance review guidelines

Appended to the reviewer prompt. Only rules a general model cannot infer.

- Money is integer rupiah; `float` anywhere in a money path is a major finding.
  Rounding must be `ROUND_HALF_UP`, applied once.
- SQL built with f-strings, `%` or concatenation is a blocker.
- A credential in source, including as an environment default, is a blocker.
  Logging one is a blocker.
- A swallowed exception on a payment or core-banking call is major.
- Outbound HTTP without a timeout is major.
- A ticket's acceptance criterion without a test is minor.
