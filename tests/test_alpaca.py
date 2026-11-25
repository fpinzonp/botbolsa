from bot.alpaca import Alpaca


def fake(responses):
    a = Alpaca("k", "s")
    calls = iter(responses)
    a.open_orders = lambda: next(calls)
    return a


def test_wait_for_fills_returns_when_no_open_orders():
    assert fake([[{"id": 1}], []]).wait_for_fills(timeout_s=5, poll_s=0)


def test_wait_for_fills_gives_up_after_timeout():
    a = Alpaca("k", "s")
    a.open_orders = lambda: [{"id": 1}]
    assert not a.wait_for_fills(timeout_s=0.05, poll_s=0.01)
