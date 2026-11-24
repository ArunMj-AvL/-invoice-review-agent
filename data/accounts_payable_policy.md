# Accounts Payable Policy — exercise version
P1. Three-way match invoice PO, goods receipt and vendor. Invoices without a matching PO are NEEDS_INFORMATION; do not approve.
P2. Billed quantity must not exceed the quantity physically received. Any excess is HOLD_FOR_REVIEW.
P3. Unit price must match PO price. Any difference greater than zero is HOLD_FOR_REVIEW. Do not silently apply tolerance.
P4. Vendor must equal PO vendor, exist in vendors.csv, and be active. Otherwise HOLD_FOR_REVIEW.
P5. Duplicate detection: same PO + vendor + billed quantity + billed unit price. An exact match between different invoice IDs is a POSSIBLE_DUPLICATE, not automatically fraud; send for human review.
P6. If all checks pass and total invoice amount is <= INR 100,000, decision may be AUTO_CLEAR (simulation only). Above INR 100,000 requires FINANCE_APPROVAL.
P7. High-risk vendors require FINANCE_APPROVAL even if below INR 100,000.
P8. No system may execute a bank transfer. Approval only writes a local audit record. Missing data always takes priority over LLM optimism.
P9. Every decision must include identifiers of matching rows and policy clauses. The invoice amount for the exercise is billed_qty * billed_unit_price_inr; ignore tax and shipping.
