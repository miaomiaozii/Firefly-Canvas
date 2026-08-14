# Software Quality System Resources

## Knowledge

- [Google Testing Blog: Just Say No to More End-to-End Tests](https://testing.googleblog.com/2015/04/just-say-no-to-more-end-to-end-tests.html)
  Explains why useful feedback must be fast, reliable, and failure-isolating, and why a small number of end-to-end journeys should sit above many focused tests.
- [Google SRE Book: Testing for Reliability](https://sre.google/sre-book/testing-reliability/)
  Primary reference for converting every reported bug into a regression test, prioritizing mission-critical surfaces, and using smoke tests as low-effort release gates.
- [Google SRE Book: Eliminating Toil](https://sre.google/sre-book/eliminating-toil/)
  Defines repetitive manual bug hunting as toil and explains why permanent engineering improvements must replace work that scales linearly.

## Wisdom (Communities)

- Firefly Canvas production feedback
  Use real user escapes to rank risk and create regression fixtures, not as the only way defects are discovered.

## Gaps

- Firefly Canvas still needs a written inventory of its critical user journeys and explicit release thresholds.
- Provider-specific protocol sources need to be recorded beside adapter contract tests when each integration is certified.
