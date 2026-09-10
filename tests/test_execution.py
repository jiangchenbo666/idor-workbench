from idor_workbench.domains.idor.execution import TestContext, _prepare_request


def test_prepare_request_keeps_raw_body_for_duplicate_json_keys():
    context = TestContext(
        base_url="http://example.test",
        token_url="/login",
    )
    request = _prepare_request(
        context,
        "TOKEN",
        {
            "method": "POST",
            "path": "/api/BasketItems",
            "raw_body": '{"ProductId":1,"BasketId":"2","quantity":1,"BasketId":"1"}',
        },
    )

    assert request["raw_body"] == '{"ProductId":1,"BasketId":"2","quantity":1,"BasketId":"1"}'
    assert request["body"] is None
    assert request["headers"]["Content-Type"] == "application/json"
