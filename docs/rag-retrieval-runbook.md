# RAG Retrieval Runbook

This runbook covers the public, sanitized TunTunAgent FAQ retrieval path. It does not contain production credentials, private prompts, or customer data.

## Current Baseline

- Corpus: 38 enabled and indexed short FAQ segments
- Chunking: preserve each source question-and-answer group
- Dataset retrieval: `keyword_search`
- Dataset `top_k`: `5`
- Dataset reranking: disabled
- Dataset score threshold: disabled
- Workflow retrieval threshold: `0`
- Empty result: route to the human-service fallback

The workflow threshold stays at `0` because Dify 1.14.2 keyword and full-text results do not always include a comparable vector score. Applying a positive second-stage threshold can discard valid keyword hits.

## Rebuild Procedure

1. Back up the Dify workflow graph, dataset retrieval configuration, keyword table, and segment keywords.
2. Confirm every target segment is enabled and has status `completed`.
3. Rebuild the Jieba keyword table through Dify's native keyword index implementation.
4. Confirm segment `keywords` fields are populated and representative terms map to the expected segment IDs.
5. Apply the dataset and workflow baseline above to both the published workflow and draft.
6. Run the regression matrix through the same FastAPI proxy used by the public application.

Do not treat a successful knowledge-node execution as a pass by itself. The node can succeed with an empty result, and a generation model can still produce an unsupported answer from unrelated context.

## Regression Matrix

| Query | Expected result |
| --- | --- |
| `押金怎么算的` | Seller sets the deposit; it is paid separately; rent plus deposit is approximately the account value; refund after verification |
| `押金多少合适` | Recommend rent plus deposit approximately equal to account value |
| `押金多久能退` | Can settle after use; no need to wait for lease expiry; usually refunded after account verification |
| `租号流程是什么` | Payment, order group, account verification, use, settlement, seller verification, payout and deposit refund |
| `你好` | Greeting branch, not FAQ generation |

For each case, verify the final answer, selected workflow branch, retrieved segment content, response source, HTTP status, and latency.

## Rollback

Rollback is configuration-first:

1. Restore the previous workflow graphs and dataset retrieval JSON from the pre-change backup.
2. Restore the keyword table and segment keywords together so they remain consistent.
3. Repeat the regression matrix and confirm the public proxy health endpoint.

Do not delete vector collections or re-index source documents during a parameter rollback unless the stored index itself is confirmed corrupt.
