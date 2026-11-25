# Invoice Review Agent (Agentic AI assignment)

An assistant that reviews vendor invoices and recommends AUTO_CLEAR, HOLD_FOR_REVIEW,
NEEDS_INFORMATION, POSSIBLE_DUPLICATE or FINANCE_APPROVAL. It never makes a payment.
LangGraph runs the workflow; LangChain, CrewAI and AutoGen explain or advise only.
Only `app/rules.py` decides.

## Team
| Member | Owns |
|---|---|
| Lead | `app/state.py`, `app/graph.py`, `app/approval.py`, `app/errors.py`, `main.py`, final README |
| Niranjan | `data/`, `app/rules.py`, `tests/test_rules.py`, `requirements.txt`, `docs/architecture.png` |
| Aamir | `app/langchain_components.py`, `app/crewai_adapter.py`, `examples/run_crew_standalone.py` |
| Anit | `app/autogen_adapter.py`, `examples/run_autogen_standalone.py`, `tests/test_graph_*.py`, `docs/benchmark.md` |

## Setup
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # Windows: copy .env.example .env
```
Keep `USE_MOCK_LLM=1` to run without an API key.

## Run
_To be filled in by the lead after integration._

## Module sections
_Each member adds a short section below: what the module does and how to run its standalone demo._

### Rules (Niranjan)
### LangChain + CrewAI (Aamir)

### LangChain explanation

`app/langchain_components.py` builds a Pydantic-validated explanation from the case facts and deterministic `rule_result`. The decision, reasons, evidence, and invoice ID are copied from the rule result; the model writes only the summary and next action. Invalid structured output is retried up to two times before an adapter error is raised.

Run with `USE_MOCK_LLM=1` to use the canned explanation without an API key.

### CrewAI invoice investigation

`app/crewai_adapter.py` exposes `run_crew(case_facts, rule_result, correlation_id="", mock=None)`. It runs an invoice analyst and vendor risk analyst and returns an advisory memo, suggested next steps, and model-call count. The crew uses only the supplied facts and cannot change the deterministic decision or initiate payment.

Run the standalone demo from the repository root:

```powershell```
python -m examples.run_crew_standalone

### AutoGen + tests + benchmark (Anit)
### LangGraph workflow (Lead)
