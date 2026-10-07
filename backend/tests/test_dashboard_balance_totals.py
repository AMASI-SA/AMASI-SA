from balances import compute_balances


def test_totals_only_preserves_money_counts_and_default_details():
    rows=[dict(shipping_company=company,order_status=status,order_status_slug=slug,
               payment_method=method,total_amount=amount,shipping_cost=cost)
          for company in ['iMile',"'iMile'",'Other']
          for status,slug in [('completed',''),('new','delivered'),('cancelled','')]
          for method in ['cash_on_delivery','عند الاستلام','mada']
          for amount,cost in [(10.005,2.675),(0,0),(-5,0.125)]]
    args=(rows,['completed','delivered'],['completed'])
    cfg={'iMile':dict(cost_per_order=7.135,vat_percent=15)}
    original=compute_balances(*args,company_cfgs=cfg)
    actual=compute_balances(*args,company_cfgs=cfg,collect_details=False)
    for kind in ('shipping','cod'):
        assert {key:value for key,value in actual[kind].items() if key not in ('by_company','by_status')}=={key:value for key,value in original[kind].items() if key not in ('by_company','by_status')}
        assert actual[kind]['by_company']==[]
        assert actual[kind]['by_status']==[]
        assert original[kind]['by_company']
        assert original[kind]['by_status']
    assert compute_balances(*args,company_cfgs=cfg)==original


def test_50000_distinct_labels_do_not_allocate_detail_buckets():
    def rows():
        for i in range(50000):
            yield dict(shipping_company='company-'+str(i),order_status='status-'+str(i),
                       payment_method='cod',total_amount=1.125,shipping_cost=0.335)
    expected=compute_balances(rows(),[],[])
    actual=compute_balances(rows(),[],[],collect_details=False)
    for kind in ('shipping','cod'):
        assert {key:value for key,value in actual[kind].items() if key not in ('by_company','by_status')}=={key:value for key,value in expected[kind].items() if key not in ('by_company','by_status')}
        assert actual[kind]['by_company']==[]
        assert actual[kind]['by_status']==[]
    assert len(expected['shipping']['by_company'])==50000


def test_empty_dataset_totals_only_matches_default():
    assert compute_balances([],[],[],collect_details=False)==compute_balances([],[],[])
