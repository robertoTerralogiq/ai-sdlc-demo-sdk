# Money and security rules (always on)

- Money is integer rupiah. Use `Decimal` for intermediate maths and round with
  `ROUND_HALF_UP` exactly once, at the end. Never `float` for money.
- SQL always uses bound parameters (`?` / `%s`). Never format a value into SQL.
- No credential in source, not even as a default. Read it from the environment
  and fail at startup when it is missing (`os.environ["NAME"]`).
- Never log a credential, token or full customer identifier (NIK, account number).
- Never swallow an exception. Outbound HTTP calls set a timeout and check status.
- Do not read files outside the workspace. Engineer machines hold real credentials
  in `~/.config`, `~/.aws`, `~/.gcloud`, `.env` — never open, print or copy them.
