# Engineering Rules

## Product principle

This project is a judicial decision-support and verification system.

It does not replace the judge and must never present an AI-generated
decision as legally authoritative.

## Core invariant

Every legally significant conclusion must be traceable whenever possible:

Conclusion
→ Legal fact
→ Evidence
→ Legal rule
→ Norm version
→ Source

## Never

- invent legal sources;
- silently apply the latest law version to historical cases;
- treat LLM output as a legal authority;
- hide uncertainty;
- use case statistics as a sentencing recommendation;
- add legal rules without tests;
- commit secrets;
- send sensitive documents to external providers by default.

## Prefer

deterministic rules > LLM reasoning
primary sources > generated explanations
structured extraction > free-form generation
hybrid retrieval > embeddings alone
explicit uncertainty > guessing
modular monolith > premature microservices
tests > demos
working software > speculative infrastructure

## Before marking work complete

Run:

- lint
- unit tests
- integration tests where applicable

Update:

docs/IMPLEMENTATION_STATUS.md
