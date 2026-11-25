# AutoGen Benchmark

## Overview
The AutoGen adapter uses a Verifier and a Skeptical Reviewer to review invoice facts and the deterministic rule result. The review is advisory and cannot override the invoice decision.

## Benchmark Setup
- Framework: AutoGen adapter
- Mode: Mock
- Scenarios: 5
- Actual LLM calls: 0
- Measurements: Execution time and model-call count

## Results

| Scenario | Execution Time (ms) | Model Calls |
|---|---:|---:|
| Clean invoice | 0.030 | 0 |
| Quantity mismatch | 0.019 | 0 |
| Price mismatch | 0.009 | 0 |
| High-risk vendor | 0.006 | 0 |
| Duplicate invoice | 0.005 | 0 |
| Total | 0.069 | 0 |
| Average | 0.014 | 0 |

## Conclusion
The mock benchmark confirms that the AutoGen adapter executes successfully for all five scenarios. The measured timings represent mock execution overhead only and do not represent real LLM inference latency or full multi-agent performance.

Real LLM benchmarking will be conducted after integration.
