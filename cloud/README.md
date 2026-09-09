# FUJI-ATLAS Cloud Runtime

Cloud deployment must run from a clean checkout and use repository secrets.
Local Mac paths, PDFs, logs, chart images, credentials, and `node_modules` are
intentionally excluded from version control.

Planned schedule (Europe/Istanbul):

- Weekdays 06:35: full macro and higher-timeframe analysis.
- Weekdays 09:15–17:15: hourly refresh.
- A 15-minute news cadence should be enabled only by a separate event trigger;
  GitHub Actions scheduled workflows are not suitable for guaranteed 15-minute
  execution.

Required secrets:

`OPENAI_API_KEY`, `EXA_API_KEY`, `FIRECRAWL_API_KEY`, `CONTEXT7_API_KEY`,
`TWELVEDATA_API_KEY`, and `METALPRICEAPI_KEY`.

The canonical source/security policy is `FUJI_DATA_POLICY.md`. The cloud runner
must use the same priority order as local runs: official macro sources, Twelve
Data, MetalpriceAPI spot fallback, XAUS spot fallback, then explicitly labelled
Yahoo futures proxy. Secrets are read from the runtime environment only.

Every run must write provider, UTC retrieval time, status, data age, bar count,
and fallback level. Missing critical data, failed model analysis, or an
incomplete knowledge-base/top-down step produces `BLOCKED` and no PDF, journal
update, Telegram message, or artifact.
