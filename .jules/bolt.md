## 2025-03-03 - Consolidate Multiple JSON Parsing Loops
**Learning:** In scenarios where we need multiple aggregations over the same set of JSON files, executing separate loops causes redundant file I/O and JSON parsing overhead, which can become a bottleneck.
**Action:** Consolidate multiple passes into a single pass, computing all required statistics concurrently in one loop. This halves the parsing time and reduces disk I/O, especially when parsing numerous files in a system designed to be lean and avoid excessive in-memory caching.
