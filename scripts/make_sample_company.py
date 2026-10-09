"""Generate the demo company's files: samples/novak_trading/ (what the agent sees) and
samples/answer_keys/novak_trading.md (what a careful accountant would find; the agent never sees it).

Novák Trading s.r.o. is a fictional Czech office-supplies wholesaler. Its files are realistically messy:
two invoice layouts, Czech number formats, one supplier writing in English, a re-sent invoice, a price that drifted
from the purchase order, an over-delivery, an invoice with no order behind it, a payment made twice, overdue
customers and a sales drop. Deterministic: same files every run.

  .venv/Scripts/python scripts/make_sample_company.py
"""
import csv
import random
import shutil
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "samples"
OUT = ROOT / "novak_trading"
KEY = ROOT / "answer_keys" / "novak_trading.md"
AS_OF = date(2026, 10, 9)
VAT = 0.21
rnd = random.Random(7)

SUPPLIERS = {
    "Papírna Brno a.s.": {"ico": "25311654", "layout": "cz", "prefix": "2026-"},
    "OfficeLine GmbH": {"ico": "DE814455210", "layout": "en", "prefix": "OL-"},
    "Tonery Praha s.r.o.": {"ico": "28196547", "layout": "cz", "prefix": "TP"},
    "Kancl Nábytek s.r.o.": {"ico": "06485921", "layout": "cz", "prefix": "KN-26/"},
}
CATALOG = {
    "Papírna Brno a.s.": [("PAP-A4-80", "Kancelářský papír A4 80g (krabice 5 bal.)", 612.0),
                          ("PAP-A3-80", "Kancelářský papír A3 80g (bal.)", 189.0),
                          ("OBA-C4", "Obálky C4 samolepicí (250 ks)", 455.0)],
    "OfficeLine GmbH": [("OL-STP-24", "Stapler 24/6, metal", 240.0), ("OL-BND-50", "Ring binder A4 50mm", 58.0),
                        ("OL-LAB-A4", "Labels A4 3x8 (100 sheets)", 365.0)],
    "Tonery Praha s.r.o.": [("TON-HP59X", "Toner HP 59X kompatibilní", 1490.0),
                            ("TON-BR2420", "Toner Brother TN-2420 kompatibilní", 690.0)],
    "Kancl Nábytek s.r.o.": [("KN-ZID-ERGO", "Kancelářská židle ERGO 300", 3890.0),
                             ("KN-STUL-160", "Psací stůl 160x80 bílý", 5450.0)],
}


def cz(n: float) -> str:
    """1250.5 -> '1 250,50' (Czech format, narrow spacing as many invoicing tools print it)."""
    s = f"{n:,.2f}".replace(",", " ").replace(".", ",")
    return s


def en(n: float) -> str:
    return f"{n:,.2f}"


def write_invoice(inv: dict, path: Path) -> None:
    sup, s = inv["supplier"], SUPPLIERS[inv["supplier"]]
    net = round(sum(q * p for _, _, q, p in inv["lines"]), 2)
    vat = round(net * VAT, 2)
    tot = round(net + vat, 2)
    inv["net"], inv["total"] = net, tot
    if s["layout"] == "cz":
        rows = "\n".join(f"{sku:<12} {desc[:40]:<40} {q:>5} ks {cz(p):>12} {cz(q * p):>14}" for sku, desc, q, p in inv["lines"])
        txt = f"""{sup.upper()}
IČO: {s['ico']}   DIČ: CZ{s['ico']}
Bankovní spojení: 2400{rnd.randint(100000, 999999)}/2010

FAKTURA - DAŇOVÝ DOKLAD č. {inv['number']}

Odběratel: Novák Trading s.r.o., Vinohradská 88, 120 00 Praha 2, IČO 27654321
Datum vystavení: {inv['date']:%d.%m.%Y}
Datum splatnosti: {inv['due']:%d.%m.%Y}
Číslo objednávky: {inv['po'] or '-'}
Variabilní symbol: {''.join(c for c in inv['number'] if c.isdigit())}

Kód          Popis                                    Množství     Cena/ks        Celkem
{rows}

Základ daně 21 %: {cz(net)} Kč
DPH 21 %: {cz(vat)} Kč
CELKEM K ÚHRADĚ: {cz(tot)} Kč
"""
    else:
        rows = "\n".join(f"{sku} | {desc} | {q} | {en(p)} | {en(q * p)}" for sku, desc, q, p in inv["lines"])
        txt = f"""INVOICE

{sup}, Industriestr. 14, 90402 Nürnberg, Germany - VAT ID {s['ico']}

Invoice number: {inv['number']}
Invoice date: {inv['date']:%Y-%m-%d}
Payment due: {inv['due']:%Y-%m-%d}
Customer: Novák Trading s.r.o. (CZ27654321)
PO reference: {inv['po'] or 'n/a'}

SKU | Description | Qty | Unit price (CZK) | Amount (CZK)
{rows}

Net amount: {en(net)} CZK
VAT 21%: {en(vat)} CZK
Total due: {en(tot)} CZK
"""
    path.write_text(txt, encoding="utf-8")


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "invoices").mkdir(parents=True)
    KEY.parent.mkdir(parents=True, exist_ok=True)

    # ---- purchase orders: 11 orders, Aug-Sep 2026 ----
    pos, n = [], 37
    for sup in ["Papírna Brno a.s.", "OfficeLine GmbH", "Tonery Praha s.r.o.", "Papírna Brno a.s.", "Kancl Nábytek s.r.o.",
                "OfficeLine GmbH", "Tonery Praha s.r.o.", "Papírna Brno a.s.", "OfficeLine GmbH", "Kancl Nábytek s.r.o.",
                "Tonery Praha s.r.o."]:
        n += 1
        d = date(2026, 8, 3) + timedelta(days=(n - 38) * 5)
        items = rnd.sample(CATALOG[sup], k=min(2, len(CATALOG[sup])))
        lines = [(sku, desc, rnd.choice([10, 20, 25, 40, 50]) if p < 1000 else rnd.choice([2, 4, 6, 10]), p) for sku, desc, p in items]
        pos.append({"po": f"PO-2026-{n:03d}", "supplier": sup, "date": d, "lines": lines})
    with (OUT / "purchase_orders.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["po_number", "supplier", "order_date", "sku", "description", "qty", "unit_price_czk"])
        for po in pos:
            for sku, desc, q, p in po["lines"]:
                w.writerow([po["po"], po["supplier"], po["date"].isoformat(), sku, desc, q, f"{p:.2f}"])

    # ---- supplier invoices: one per PO, then the planted problems ----
    invoices, findings = [], []
    for i, po in enumerate(pos):
        sup = po["supplier"]
        idt = po["date"] + timedelta(days=rnd.randint(4, 9))
        num = f"{SUPPLIERS[sup]['prefix']}{1100 + i * 7}"
        inv = {"supplier": sup, "number": num, "date": idt, "due": idt + timedelta(days=14), "po": po["po"],
               "lines": [list(l) for l in po["lines"]], "file": ""}
        invoices.append(inv)
    # price drift: invoice charges more than the PO price
    pd_inv = invoices[3]
    sku, desc, q, p = pd_inv["lines"][0]
    pd_inv["lines"][0][3] = round(p * 1.12, 2)
    findings.append(("Price mismatch", pd_inv, f"{sku}: invoiced {cz(pd_inv['lines'][0][3])} Kč/unit, PO {pd_inv['po']} says {cz(p)} Kč/unit "
                     f"(+12 %, overcharge {cz(round((pd_inv['lines'][0][3] - p) * q * (1 + VAT), 2))} Kč incl. VAT)"))
    # over-delivery: invoice quantity above the ordered quantity
    od_inv = invoices[5]
    sku, desc, q, p = od_inv["lines"][1]
    od_inv["lines"][1][2] = q + 15
    findings.append(("Quantity exceeds PO", od_inv, f"{sku}: invoiced {q + 15} units, PO {od_inv['po']} ordered {q} "
                     f"(15 extra units, {cz(round(15 * p * (1 + VAT), 2))} Kč incl. VAT)"))
    # invoice with no purchase order behind it
    nopo = {"supplier": "Tonery Praha s.r.o.", "number": "TP1199", "date": date(2026, 9, 22), "due": date(2026, 10, 6),
            "po": None, "lines": [["TON-HP59X", "Toner HP 59X kompatibilní", 6, 1490.0]], "file": ""}
    invoices.append(nopo)
    findings.append(("No purchase order", nopo, "invoice has no PO reference and matches no open order"))
    for inv in invoices:
        tag = inv["supplier"].split()[0].lower().replace("á", "a").replace("í", "i")
        inv["file"] = f"invoices/{tag}_{inv['number'].replace('/', '-')}.txt"
        write_invoice(inv, OUT / inv["file"])
    # duplicate: the same Papírna invoice re-sent a week later as a separate document
    dup = invoices[0]
    dup_file = f"invoices/papirna_{dup['number']}_RESENT.txt"
    (OUT / dup_file).write_text("Upomínka / znovu zasíláme fakturu\n\n" + (OUT / dup["file"]).read_text(encoding="utf-8"), encoding="utf-8")
    findings.append(("Duplicate invoice", dup, f"{dup_file} is the same invoice {dup['number']} ({cz(dup['total'])} Kč) re-sent"))

    # ---- bank statement: supplier payments out, customer receipts in ----
    bank = []
    for inv in invoices:
        if inv is nopo:
            continue
        if inv["due"] <= AS_OF:
            bank.append((inv["due"] - timedelta(days=rnd.randint(0, 2)), -inv["total"], inv["supplier"],
                         "".join(c for c in inv["number"] if c.isdigit()), f"Faktura {inv['number']}"))
    # the duplicate got paid twice
    bank.append((dup["due"] + timedelta(days=6), -dup["total"], dup["supplier"],
                 "".join(c for c in dup["number"] if c.isdigit()), f"Faktura {dup['number']}"))
    findings.append(("Paid twice", dup, f"invoice {dup['number']} was paid twice ({cz(dup['total'])} Kč each); "
                     f"{cz(dup['total'])} Kč to recover"))

    # ---- customer invoices (receivables) ----
    customers = ["Alza Office s.r.o.", "Kanceláře Plus a.s.", "Škola Hostivař", "Moravia Consult s.r.o.",
                 "Městský úřad Kolín", "BrightDesk s.r.o."]
    recv = []
    for k in range(22):
        c = customers[k % len(customers)]
        idt = date(2026, 7, 1) + timedelta(days=k * 4)
        amt = round(rnd.uniform(18000, 140000), 2)
        due = idt + timedelta(days=30 if "úřad" not in c else 45)
        paid = None
        if due < AS_OF and not (c in ("BrightDesk s.r.o.", "Moravia Consult s.r.o.") and k > 8):
            paid = due + timedelta(days=rnd.randint(-3, 6))
            if paid > AS_OF:
                paid = None
        recv.append({"invoice_no": f"FV2026{k + 301:04d}", "customer": c, "issue_date": idt, "due_date": due,
                     "amount_czk": amt, "paid": paid})
        if paid:
            bank.append((paid, amt, c, f"2026{k + 301:04d}", f"Úhrada FV2026{k + 301:04d}"))
    with (OUT / "customer_invoices.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["cislo_faktury", "odberatel", "datum_vystaveni", "datum_splatnosti", "castka_kc"])
        for r in recv:
            w.writerow([r["invoice_no"], r["customer"], r["issue_date"].strftime("%d.%m.%Y"),
                        r["due_date"].strftime("%d.%m.%Y"), cz(r["amount_czk"])])
    # fixed monthly costs
    for m in (7, 8, 9, 10):
        if date(2026, m, 5) <= AS_OF:
            bank.append((date(2026, m, 5), -68000.0, "Realitní správa Vinohrady", "0726", "Nájem skladu"))
        if date(2026, m, 12) <= AS_OF:
            bank.append((date(2026, m, 12), -412500.0, "Mzdy", "", "Výplaty zaměstnanců"))
    bank.sort(key=lambda r: r[0])
    bal = 1_850_000.0
    with (OUT / "bank_statement.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Datum", "Částka", "Měna", "Protistrana", "VS", "Zpráva", "Zůstatek"])
        for d, a, who, vs, msg in bank:
            bal = round(bal + a, 2)
            w.writerow([d.strftime("%d.%m.%Y"), cz(a), "CZK", who, vs, msg, cz(bal)])

    # ---- sales by month: one big customer stops ordering toner in September ----
    with (OUT / "sales_2026.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["month", "customer", "category", "units", "revenue_czk"])
        for m in range(1, 10):
            for c in customers:
                for cat, base in (("paper", 52000), ("toner", 38000), ("furniture", 30000), ("small supplies", 14000)):
                    rev = base * rnd.uniform(0.85, 1.15) * (1.03 ** m)
                    if c == "Kanceláře Plus a.s." and cat == "toner" and m == 9:
                        rev = 0.0
                    if cat == "furniture" and rnd.random() < 0.4:
                        rev = 0.0
                    w.writerow([f"2026-{m:02d}", c, cat, int(rev // 600), round(rev, 2)])

    # ---- customer feedback ----
    fb = [("Toner TN-2420 leaked inside the printer, second time this month", "quality"),
          ("Delivery came two days late again", "delivery"), ("Great prices on paper, fast delivery", "praise"),
          ("Invoice had the wrong VAT number", "billing"), ("Chair ERGO 300 armrest broke after a week", "quality"),
          ("Driver was very helpful", "praise"), ("Order arrived incomplete, missing binders", "delivery"),
          ("Toner prints streaks, HP 59X compatible", "quality"), ("Why was I invoiced twice for one order?", "billing"),
          ("Toner cartridge empty after 300 pages", "quality")]
    with (OUT / "customer_feedback.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "customer", "channel", "text"])
        for k in range(36):
            t, _ = fb[k % len(fb)] if k % 3 else fb[(k * 7) % len(fb)]
            w.writerow([(date(2026, 7, 1) + timedelta(days=k * 2 + (k % 3))).isoformat(), customers[k % len(customers)],
                        rnd.choice(["email", "phone", "web form"]), t])

    # ---- answer key (never copied to the inbox) ----
    overdue = [r for r in recv if not r["paid"] and r["due_date"] < AS_OF]
    open_recv = [r for r in recv if not r["paid"]]
    unpaid_sup = [i for i in invoices if i["due"] > AS_OF or i is nopo]
    lines = [f"# Answer key: Novák Trading s.r.o. (as of {AS_OF:%d.%m.%Y})", "",
             "Generated with the files by scripts/make_sample_company.py. The agent never sees this file.", "",
             "## Invoice vs purchase-order reconciliation", ""]
    lines += [f"- **{k}** · {inv['file'] if k != 'Duplicate invoice' else dup_file} · {inv['supplier']} · {inv['number']}: {t}"
              for k, inv, t in findings]
    lines += ["", f"Invoices: {len(invoices) + 1} files ({len(invoices)} distinct + 1 re-sent duplicate). "
              f"Purchase orders: {len(pos)}.", "", "## Receivables", "",
              f"Overdue customer invoices: {len(overdue)}, {cz(sum(r['amount_czk'] for r in overdue))} Kč in total"]
    lines += [f"- {r['invoice_no']} · {r['customer']} · due {r['due_date']:%d.%m.%Y} · {cz(r['amount_czk'])} Kč "
              f"({(AS_OF - r['due_date']).days} days overdue)" for r in overdue]
    lines += ["", f"Open (unpaid) customer invoices: {len(open_recv)}, {cz(sum(r['amount_czk'] for r in open_recv))} Kč",
              f"Unpaid supplier invoices: {len(unpaid_sup)}, {cz(sum(i['total'] for i in unpaid_sup))} Kč",
              f"Bank balance on the last statement line: {cz(bal)} Kč",
              "Fixed monthly costs: rent 68 000 Kč (5th), payroll 412 500 Kč (12th).", "",
              "## Sales", "", "- Kanceláře Plus a.s. bought no toner in 2026-09 (it bought toner every month before): "
              "the main driver of the September toner drop.", "",
              "## Feedback", "", "- Most frequent complaint theme: toner quality (leaks, streaks, low yield)."]
    KEY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {sum(1 for _ in OUT.rglob('*') if _.is_file())} files to {OUT} and the answer key to {KEY}")


if __name__ == "__main__":
    main()
