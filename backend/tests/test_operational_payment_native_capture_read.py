"""Bounded owner-only original gateway capture read uses no money writes."""
import asyncio
import copy
import sys
import types
import unittest
from unittest.mock import patch

from operational_payment_native_capture_read import (
    preview_one_native_capture, _bounded_order_number,
)
from test_operational_payment_native_capture_candidate import row, NOW
from test_operational_payment_direct_order_page import paid_source, source


class Timeout:
    def __init__(self,*args,**kwargs):pass
    def __enter__(self):return self
    def __exit__(self,*args):return False


class Cursor:
    def __init__(self,data,calls):
        self.data,self.calls=data,calls
        self.limit_n=None
    def limit(self,n):
        self.limit_n=n
        return self
    async def to_list(self,*,length):
        self.calls.append(("read",self.limit_n,length))
        return copy.deepcopy(self.data[:length])


class Table:
    def __init__(self,name,docs,calls):
        self.name,self.docs,self.calls=name,docs,calls
    def find(self,query,projection):
        self.calls.append((self.name,query,projection))
        if self.name=="unified_orders":
            assert query=={"user_id":"merchant1","order_number":"301556476"}
        else:
            assert query=={"user_id":"merchant1","order_number":"301556476",
                           "provider_order_id":"580626783","source":"salla_transactions"}
        assert projection["_id"]==0
        return Cursor(self.docs,self.calls)


class Db:
    def __init__(self,orders,transactions):
        self.calls=[]
        self.tables={
            "unified_orders":Table("unified_orders",orders,self.calls),
            "order_payment_transactions_v2":Table(
                "order_payment_transactions_v2",transactions,self.calls),
        }
    def __getitem__(self,name):return self.tables[name]


def saved_order():
    return {**source(),"raw_by_source":{"salla_direct":paid_source()}}


def run(db):
    fake_mongo=types.ModuleType("pymongo")
    fake_mongo.timeout=Timeout
    budget=types.ModuleType("operational_demand_refresh")
    budget.calculation_checkpoint=lambda:None
    reader=types.ModuleType("operational_read_runtime")
    async def read_source_cursor(cursor,count):
        return await cursor.to_list(length=count)
    reader.read_source_cursor=read_source_cursor
    with patch.dict(sys.modules,{
        "pymongo":fake_mongo,"operational_demand_refresh":budget,
        "operational_read_runtime":reader,
    }):
        return asyncio.run(preview_one_native_capture(
            db,"merchant1","301556476",as_of=NOW))


class NativeReadTests(unittest.TestCase):
    def test_one_owner_order_one_complete_transaction_read_only(self):
        db=Db([saved_order()],[row()])
        before=copy.deepcopy([db.tables["unified_orders"].docs,
                              db.tables["order_payment_transactions_v2"].docs])
        result=run(db)
        self.assertEqual(result["status"],"verified_native_capture")
        self.assertFalse(result["posting_permitted"])
        self.assertEqual(result["original_captured_at"],"2026-11-23T23:51:00+00:00")
        self.assertEqual(before,[db.tables["unified_orders"].docs,
                                 db.tables["order_payment_transactions_v2"].docs])
        self.assertEqual(len([c for c in db.calls if c[0]=="read"]),2)

    def test_duplicate_canonical_order_records_never_select_first(self):
        db=Db([saved_order(), saved_order()],[row()])
        result=run(db)
        self.assertEqual(result["reason"],"canonical_salla_order_count_invalid")
        self.assertEqual(len(db.calls),2)

    def test_overfull_source_evidence_is_not_partially_accepted(self):
        db=Db([saved_order()],[row() for _ in range(33)])
        result=run(db)
        self.assertEqual(result["reason"],"transaction_rows_incomplete")
        self.assertFalse(result["posting_permitted"])

    def test_missing_native_transactions_are_not_payment(self):
        db=Db([saved_order()],[])
        result=run(db)
        self.assertEqual(result["status"],"not_verified")
        self.assertNotIn("original_captured_at",result)

    def test_order_scope_is_filtered_before_proof_read(self):
        db=Db([], [row()])
        result=run(db)
        self.assertEqual(result["reason"],"canonical_salla_order_count_invalid")
        self.assertEqual(len([x for x in db.calls if x[0]=="order_payment_transactions_v2"]),0)

    def test_only_digit_order_number_accepted(self):
        for value in ("", "../", "3012 ABC", "٥٨٧", "1"*33):
            with self.assertRaisesRegex(ValueError,"payment_order_number_invalid"):
                _bounded_order_number(value)
        self.assertEqual(_bounded_order_number("301556476"),"301556476")


if __name__=="__main__":
    unittest.main()
