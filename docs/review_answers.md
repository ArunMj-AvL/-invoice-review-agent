# Demonstration review questions: prepared answers

**1. Why is an AutoGen debate not allowed to change a deterministic price mismatch?**
A price mismatch is a fact: billed price minus PO price, computed in `Decimal`. Policy P3 says any difference is a hold, with no silent tolerance. An LLM debate is probabilistic and can be talked into agreeing with itself, and the policy is the authority, not the model. So AutoGen only returns `AGREE` or `CHALLENGE` with concerns, and `deterministic_decision` copies the decision from `rule_result` without reading the agent output. A test feeds an agent that claims `AUTO_CLEAR`, and the decision is unchanged.

**2. How do you avoid rerunning the CrewAI crew on graph resume?**
LangGraph checkpoints the state after every node. The crew runs in its own node (`crew_reconciliation`) and its memo is stored in the state. When the human answers, the graph resumes at `human_approval`, and completed nodes are not executed again. `human_approval` is kept separate and does nothing with side effects before `interrupt()`, because LangGraph re-runs the interrupted node from its start. The test `test_interrupt_then_resume_same_thread_does_not_rerun_crew` asserts that the crew was called exactly once.

**3. When do you use an interrupt rather than returning a string "approval required"?**
Use an interrupt when the workflow must actually stop, keep its state, and continue later on the same thread. A returned string only tells the caller something, and nothing enforces it. The next step could still run, and the context would be lost. With `interrupt()` plus a checkpointer, nothing after it executes until a valid resume arrives, the state survives a restart (SQLite checkpoints), and the resume carries a validated actor and action. A rejected or malformed resume is a reject.

**4. Why is an invoice candidate marked duplicate rather than fraudulent?**
P5 matches on PO, vendor, quantity and price. That is only evidence of a possible duplicate. It can be an honest re-send, a resubmitted webhook, or a split delivery billed separately. The system cannot know intent, and accusing a vendor of fraud on a pattern match would be wrong and harmful. So the status is `POSSIBLE_DUPLICATE`, always routed to a human, and the explanation text says it is not a fraud finding.
