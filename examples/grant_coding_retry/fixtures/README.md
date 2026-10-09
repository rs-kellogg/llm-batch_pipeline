# Recorded failed-parent fixtures

These fixtures are the terminal parent runs used by the retry example. They
contain provider-native request and response artifacts over synthetic grant
data, along with normalized results, three `missing_output` failures, audits,
state, and environment provenance.

- `openai/20261008T234504Z_openai_760d2c9d` records an OpenAI parent run.
- `anthropic/20261008T234934Z_anthropic_132a1977` records an Anthropic parent run.

Both parents contain five processed segments, 30 expected records, 27 valid
results, and missing records `MRETRY-002`, `MRETRY-014`, and `MRETRY-026`.
They were captured on 2026-10-08. Provider batch and uploaded-file identifiers
are retained as provenance; they are not credentials.

Personal absolute paths were replaced with `__EXAMPLE_ROOT__`. Run
`../setup-parent.sh PROVIDER` to verify the SHA-256 inventory, copy one fixture
under the ignored `runs/` directory, and resolve those placeholders for the
current checkout. The tracked fixtures are historical, terminal artifacts and
must not be submitted or synchronized.
