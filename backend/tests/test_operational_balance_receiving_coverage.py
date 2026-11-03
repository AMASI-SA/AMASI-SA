"""Synthetic receiving invoices against a saved operational snapshot; no financial writes."""
import unittest
from datetime import datetime
from copy import deepcopy
from operational_receiving_coverage import compare_receiving

START = "2026-09-01T00:00:00+03:00"
AT = "2026-09-03T02:00:00+03:00"
NOW = "2026-09-04T12:00:00+03:00"


def invoice(i="test-invoice", pieces=("piece-first", "piece-second"), unit=1750):
    return dict(id=i, user_id="shop", invoice_number="TEST-01", supplier_id="maker", approved_at=AT,
                status="payable_posted", total_halalas=len(pieces)*unit,
                lines=[dict(piece_ids=list(pieces), quantity=len(pieces), product_price_authority="mezan_v2",
                            product_unit_price_halalas=unit)])


def state():
    return dict(owner_id="shop", status="active", started_at=START, openings={"keep": "opening"},
                movements=[{"id": "historical", "allocations": []}],
                engine={"as_of": NOW, "obligations": {}, "facts": {}})


def save(s, inv, piece, *, currency="SAR", link=True, component="product", gross="17.50"):
    rid=f"receipt:{inv['id']}:{piece}:{component}"
    iid=piece if component=="product" else f"{piece}:{component}"
    key=f"supplier:order:{iid}:{rid}"
    s["engine"]["facts"][rid]=dict(order_id="order", item_id=iid, supplier_id="maker", accepted_at=AT, amount=gross)
    s["engine"]["obligations"][key]=dict(id=key, kind="supplier", party_type="supplier", direction="payable",
        party_id="maker", currency=currency, gross_amount=gross, confirmed=gross, evidence_ids=[rid],
        **({"source_invoice_id":inv["id"]} if link else {}))
    return key


class ReceivingAudit(unittest.TestCase):
    def test_missing_unit_and_frozen_other_currency_are_distinct_and_read_only(self):
        s,i=state(),invoice(pieces=("covered","misclassified","absent"))
        save(s,i,"covered"); k=save(s,i,"misclassified",currency="QAR",link=False)
        s["movements"][0]["allocations"]=[{"obligation_id":k,"amount":"4.00"}]
        initial=deepcopy(s)
        r=compare_receiving(s,[i],owner="shop")
        self.assertEqual(s,initial); self.assertIs(r["posting_allowed"],False)
        row=r["rows"][0]; self.assertEqual(row["invoice_gross_sar"],"52.50")
        self.assertEqual(row["matched_components"],1)
        self.assertEqual({(x["piece_id"],x["status"],x["source_amount_sar"],x.get("saved_currency")) for x in row["differences"]},
            {("misclassified","wrong_currency","17.50","QAR"),("absent","missing","17.50",None)})
        self.assertEqual(row["differences"][0 if row["differences"][0]["piece_id"]=="misclassified" else 1]["saved_gross"],"17.50")
        self.assertNotIn("suggested_payable",row)

    def test_exact_gross_not_outstanding_and_legacy_link_is_visible(self):
        s,i=state(),invoice(); k=save(s,i,"piece-first"); save(s,i,"piece-second",link=False)
        s["engine"]["obligations"][k]["confirmed"]="10.00"
        s["movements"][0]["allocations"]=[{"obligation_id":k,"amount":"6.00"}]
        r=compare_receiving(s,[i],owner="shop")["rows"][0]
        self.assertEqual(r["matched_components"],1)
        self.assertEqual([x["status"] for x in r["differences"]],["missing_provenance"])

    def test_excludes_outside_saved_window_but_identifies_newer_invoices(self):
        s,i=state(),invoice()
        source=[i,dict(i,id="future",approved_at="2026-09-08T00:00:01Z"),dict(i,id="earlier",approved_at="2025-01-01T00:00:00Z"),
                dict(i,id="demo",experiment_mode=True),dict(i,id="draft",status="draft")]
        r=compare_receiving(s,source,owner="shop")
        self.assertEqual([x["invoice_id"] for x in r["rows"]],["test-invoice"])
        self.assertEqual(r["after_snapshot_count"],1)
        self.assertEqual(compare_receiving(s,source,owner="shop",observed_at=NOW)["after_snapshot_count"],0)
        with self.assertRaisesRegex(ValueError, "stale_snapshot"):
            compare_receiving(s,source,owner="shop",observed_at=START)

    def test_bson_dates_are_utc_but_unzoned_strings_are_rejected(self):
        s,i=state(),invoice()
        i["approved_at"]=datetime(2026,9,2,23)
        self.assertEqual(compare_receiving(s,[i],owner="shop")["rows"][0]["approved_at"],
                         "2026-09-02T23:00:00+00:00")
        with self.assertRaises(ValueError):
            compare_receiving(s,[dict(i,approved_at="2026-09-02 23:00:00")],owner="shop")

    def test_extra_saved_evidence_is_not_counted_as_a_matched_invoice(self):
        s,i=state(),invoice(pieces=("ok",)); save(s,i,"ok"); save(s,i,"unexpected")
        r=compare_receiving(s,[i],owner="shop")
        self.assertEqual(r["review_count"],1)
        self.assertEqual(r["rows"][0]["extra_saved_count"],1)

    def test_product_and_service_are_independent_with_exact_gross_and_cutoff_inclusive(self):
        s,i=state(),invoice(pieces=("service-piece",),unit=1000)
        s["started_at"]=AT
        i["total_halalas"]=1425
        i["lines"][0]["services"]=[{"service_id":"work", "quantity_per_piece":1, "unit_price_halalas":425}]
        save(s,i,"service-piece",gross="10.00")
        r=compare_receiving(s,[i],owner="shop")["rows"][0]
        self.assertEqual(r["component_count"],2)
        self.assertEqual(r["differences"],[dict(piece_id="service-piece",component="service:work",
                                                status="missing",source_amount_sar="4.25")])

    def test_invalid_declared_line_total_withholds_the_comparison(self):
        s,i=state(),invoice()
        i["lines"][0]["total_halalas"]=1
        r=compare_receiving(s,[i],owner="shop")["rows"][0]
        self.assertIsNotNone(r["source_issue"])
        self.assertIsNone(r["invoice_gross_sar"])

    def test_ambiguity_or_invalid_evidence_cannot_look_matched(self):
        s,i=state(),invoice(); k=save(s,i,"piece-first")
        s["engine"]["obligations"]["duplicate"]={**s["engine"]["obligations"][k],"id":"duplicate"}
        s["engine"]["facts"].pop("receipt:test-invoice:piece-first:product")
        r=compare_receiving(s,[i],owner="shop")["rows"][0]
        self.assertIn("ambiguous_evidence",[x["status"] for x in r["differences"]])
        self.assertEqual(r["matched_components"],0)
        with self.assertRaises(ValueError): compare_receiving(s,[dict(i,user_id="another")],owner="shop")
        with self.assertRaises(ValueError): compare_receiving(s,[i,i],owner="shop")

if __name__=="__main__": unittest.main()


class NarrowSnapshot(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_owner_snapshot_does_not_read_orders_ledger_banks_or_movements(self):
        from operational_balance_store import STATES
        from operational_supplier_invoice_preview import SnapshotReads, _receiving_only
        s,i=state(),invoice()
        s["revision"]=17
        data={"settings":{}, "mz2_atomic_owners":{}, STATES:s, "mezan_supplier_invoices_v2":[i]}
        reads=[]
        session=object()

        class DB:
            def __getitem__(self, name):
                value=data[name]  # Any unrelated read, e.g. unified_orders or purchase_invoices, fails.
                class Collection:
                    async def find_one(self,q,projection=None,**kw):
                        reads.append((name,q,kw,projection))
                        return deepcopy(value)
                    def find(self,q,projection=None,**kw):
                        reads.append((name,q,kw,projection))
                        class Page:
                            async def to_list(self,length):
                                assert 0 < length <= 20001
                                return deepcopy(value)
                        return Page()
                return Collection()

        result=await _receiving_only(SnapshotReads(DB(),"shop",session),"shop",NOW)
        self.assertTrue(result["snapshot_consistent"]); self.assertEqual(result["state_revision"],17)
        self.assertEqual(result["rows"][0]["component_count"],2)
        self.assertEqual({name for name,*_ in reads},set(data)); self.assertEqual(len(reads),4)
        for name,query,kw,_ in reads:
            self.assertIs(kw["session"],session)
            self.assertEqual(query["_id" if name in {STATES,"mz2_atomic_owners"} else "user_id"],"shop")
        self.assertNotIn("lines",reads[-1][3])
