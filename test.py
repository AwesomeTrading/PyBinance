#!/usr/bin/env python3
from pybinance import PyBinance


def main():
    api = PyBinance(
        config=dict(
            # future
            # apiKey=
            # "a7cc06cad7f1f08c8454a3f2ef0886490ae12a2ff3ec3184287bccf7c1207570",
            # secret=
            # "9da25f276b0bfc1d35720ec047cbbafc1f979426888790d1b930db094d42c4d8",
            # options={'defaultType': 'future'},

            # spot
            apiKey=
            "2Me9RWGTJ3mXRkNZpYDVR4VEQ8QqUUXzbeYByrfBrZwGDyIeDdM8D7YXeROPOBV1",
            secret=
            "7i7YuBECXqRCjwrra7tJqT5uV30YDX9O1d7OZfyIOPVot5RO33q9s3UtyBpZSn7W",
            options={'defaultType': 'spot'},
        ),
        currency="USDT",
        sandbox=True,
    )
    # store.subscribe_account()
    # store.subscribe_bars(['btcusdt'], '5m')
    # store._t_loop_stream()
    # balance
    balance = api.get_my_wallet_balance()
    print(f"Balance {balance}")

    # create order
    order = api.create_my_order(symbol="BNB/USDT",
                                type="market",
                                side="SELL",
                                amount=1,
                                price=None,
                                params={})
    print(f"Order {order}")


if __name__ == '__main__':
    main()
