## Why

<!-- The problem, and the failure mode this fixes. Link the issue: "Fixes #123". -->

## What changed

<!-- One to three sentences. For a fix, say what was broken and how it behaves now. -->

## How to test

<!--
Steps a reviewer can follow, and the commands that run the relevant tests.
A bug fix needs a test that fails without it; a feature needs tests.
-->

```bash
make test && make lint
```

## Checklist

- [ ] Tests cover the change (including the permission matrix, if an endpoint was added or moved)
- [ ] `make lint` and the console build pass
- [ ] Docs and `.env.example` updated for any new or changed configuration key
- [ ] No real domains, addresses, credentials or internal hostnames in the diff
- [ ] For UI changes: before/after screenshots below

| Before | After |
| --- | --- |
|  |  |
