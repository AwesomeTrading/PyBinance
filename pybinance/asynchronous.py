from __future__ import absolute_import, division, print_function, unicode_literals

import time
import logging
import threading
import queue
import re
import json
import math
from datetime import datetime
from functools import wraps

import ccxt.pro as ccxt
from ccxt.base.errors import NetworkError, ExchangeError

logger = logging.getLogger("PyBinance")


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class PyBinanceAPI:
    def __init__(
        self,
        apiKey,
        secret,
        type="spot",
        sandbox=True,
        options: dict = {},
        **kwargs,
    ):
        options.update(
            {
                "defaultType": type,
                "sandboxMode": sandbox,
                "warnOnFetchOpenOrdersWithoutSymbol": False,
            }
        )
        config = dict(
            apiKey=apiKey,
            secret=secret,
            enableRateLimit=True,
            options=options,
        )
        match type:
            case "future":
                exchangecls = ccxt.binanceusdm
            case _:
                exchangecls = ccxt.binance

        self.exchange = exchangecls(config)

        # Must call sanbox function instead of option sandboxMode
        self.exchange.set_sandbox_mode(sandbox)
        logger.info("Starting API exchange class: %s", exchangecls)

    async def start(self):
        await self.exchange.load_markets()

    async def stop(self):
        await self.exchange.close()

    # Public functions
    async def fetch_trades(self, symbol, since=None, limit=None, params={}):
        return await self.exchange.watch_trades(symbol, since, limit, params)

    async def fetch_time(self):
        return self.exchange.milliseconds()

    async def fetch_tickers(self, symbols):
        return await self.exchange.fetch_tickers(symbols)

    async def fetch_markets(self):
        return self.exchange.markets

    async def fetch_ohlcv(self, *args, **kwargs):
        return await self.exchange.fetch_ohlcv(*args, **kwargs)

    async def watch_tickers(self, markets, **kwargs):
        return await self.exchange.watch_tickers(markets)

    # Account functions
    async def fetch_my_balance(self, params={}):
        return await self.exchange.fetch_balance(params)

    async def create_my_order(self, symbol, type, side, amount, price, params):
        return await self.exchange.create_order_ws(
            symbol=symbol,
            type=type,
            side=side,
            amount=amount,
            price=price,
            params=params,
        )

    async def fetch_my_order(self, oid, symbol):
        return await self.exchange.fetch_order(oid, symbol)

    async def fetch_my_orders(self, symbol=None, since=None, limit=None, params={}):
        return await self.exchange.fetch_open_orders(symbol, since, limit, params)

    async def fetch_my_open_orders(
        self, symbol=None, since=None, limit=None, params={}
    ):
        return await self.exchange.fetch_open_orders(symbol, since, limit, params)

    async def cancel_my_order(self, id, symbol):
        return await self.exchange.cancel_order_ws(id, symbol)

    async def cancel_my_orders(self, symbol=None, params={}):
        return await self.exchange.cancel_all_orders_ws(symbol, params)

    async def fetch_my_trades(self, symbol=None, since=None, limit=None, params={}):
        return await self.exchange.fetch_my_trades(symbol, since, limit, params)

    async def fetch_my_position(self, symbol: str, params={}):
        return await self.exchange.fetch_position(symbol, params)

    async def fetch_my_positions(self, symbols=None, params={}):
        return await self.exchange.fetch_positions(symbols, params)

    async def watch_my_balance(self, params={}, **kwargs):
        return await self.exchange.watch_balance(params)


class PyBinance(PyBinanceAPI, metaclass=Singleton):
    pass
