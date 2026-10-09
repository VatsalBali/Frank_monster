# Answer key: Novák Trading s.r.o. (as of 09.10.2026)

Generated with the files by scripts/make_sample_company.py. The agent never sees this file.

## Invoice vs purchase-order reconciliation

- **Price mismatch** · invoices/papirna_2026-1121.txt · Papírna Brno a.s. · 2026-1121: PAP-A3-80: invoiced 211,68 Kč/unit, PO PO-2026-041 says 189,00 Kč/unit (+12 %, overcharge 274,43 Kč incl. VAT)
- **Quantity exceeds PO** · invoices/officeline_OL-1135.txt · OfficeLine GmbH · OL-1135: OL-LAB-A4: invoiced 65 units, PO PO-2026-043 ordered 50 (15 extra units, 6 624,75 Kč incl. VAT)
- **No purchase order** · invoices/tonery_TP1199.txt · Tonery Praha s.r.o. · TP1199: invoice has no PO reference and matches no open order
- **Duplicate invoice** · invoices/papirna_2026-1100_RESENT.txt · Papírna Brno a.s. · 2026-1100: invoices/papirna_2026-1100_RESENT.txt is the same invoice 2026-1100 (16 552,80 Kč) re-sent
- **Paid twice** · invoices/papirna_2026-1100.txt · Papírna Brno a.s. · 2026-1100: invoice 2026-1100 was paid twice (16 552,80 Kč each); 16 552,80 Kč to recover

Invoices: 13 files (12 distinct + 1 re-sent duplicate). Purchase orders: 11.

## Receivables

Overdue customer invoices: 4, 442 019,49 Kč in total
- FV20260310 · Moravia Consult s.r.o. · due 05.09.2026 · 75 840,00 Kč (34 days overdue)
- FV20260312 · BrightDesk s.r.o. · due 13.09.2026 · 107 201,44 Kč (26 days overdue)
- FV20260316 · Moravia Consult s.r.o. · due 29.09.2026 · 126 218,92 Kč (10 days overdue)
- FV20260318 · BrightDesk s.r.o. · due 07.10.2026 · 132 759,13 Kč (2 days overdue)

Open (unpaid) customer invoices: 9, 779 104,83 Kč
Unpaid supplier invoices: 2, 56 168,20 Kč
Bank balance on the last statement line: 1 171 979,92 Kč
Fixed monthly costs: rent 68 000 Kč (5th), payroll 412 500 Kč (12th).

## Sales

- Kanceláře Plus a.s. bought no toner in 2026-09 (it bought toner every month before): the main driver of the September toner drop.

## Feedback

- Most frequent complaint theme: toner quality (leaks, streaks, low yield).
