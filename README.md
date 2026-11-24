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
### AutoGen + tests + benchmark (Anit)
### LangGraph workflow (Lead)
