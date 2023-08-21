from __future__ import absolute_import, division, print_function, unicode_literals

import logging
import re
import json
import asyncio
from unicorn_binance_websocket_api import BinanceWebSocketApiManager

import ccxt.pro as ccxt

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
                "OHLCVLimit": 1,
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
        # self.exchange.verbose = True
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

    # Ticker
    async def fetch_tickers(self, symbols):
        return await self.exchange.fetch_tickers(symbols)

    async def watch_tickers(self, symbols, **kwargs):
        return await self.exchange.watch_tickers(symbols)

    # Market
    async def fetch_markets(self):
        return self.exchange.markets

    # Bar
    async def fetch_ohlcv(self, *args, **kwargs):
        return await self.exchange.fetch_ohlcv(*args, **kwargs)

    async def watch_ohlcv(self, *args, **kwargs):
        return await self.exchange.watch_ohlcv(*args, **kwargs)

    # Account functions
    # balance
    async def fetch_my_balance(self, params={}):
        return await self.exchange.fetch_balance(params)

    async def watch_my_balance(self, params={}, **kwargs):
        return await self.exchange.watch_balance(params)

    # Order
    async def create_my_order(self, symbol, type, side, amount, price, params):
        return await self.exchange.create_order(
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
        return await self.exchange.fetch_orders(symbol, since, limit, params)

    async def fetch_my_open_orders(
        self, symbol=None, since=None, limit=None, params={}
    ):
        return await self.exchange.fetch_open_orders(symbol, since, limit, params)

    async def cancel_my_order(self, id, symbol):
        return await self.exchange.cancel_order(id, symbol)

    async def cancel_my_orders(self, symbol=None, params={}):
        return await self.exchange.cancel_all_orders(symbol, params)

    # Trade
    async def fetch_my_trades(self, symbol=None, since=None, limit=None, params={}):
        return await self.exchange.fetch_my_trades(symbol, since, limit, params)

    # Position
    async def fetch_my_position(self, symbol: str, params={}):
        return await self.exchange.fetch_position(symbol, params)

    async def fetch_my_positions(self, symbols=None, params={}):
        return await self.exchange.fetch_positions(symbols, params)


class PyBinanceWS(PyBinanceAPI):
    handlers: dict
    market_type: str

    def __init__(self, type, handlers, sandbox=True, **kwargs):
        super().__init__(type=type, sandbox=sandbox, **kwargs)
        self.handlers = handlers
        self.market_type = type
        # Binance websocket init
        exchange_path = "binance.com"
        if self.market_type == "margin":
            exchange_path = f"{exchange_path}-margin"
        elif self.market_type == "future":
            exchange_path = f"{exchange_path}-futures"

        if sandbox:
            exchange_path = f"{exchange_path}-testnet"

        logger.info("Starting websocket exchange: %s", exchange_path)
        self.ws = BinanceWebSocketApiManager(
            exchange=exchange_path,
            disable_colorama=True,
        )

    def stop(self):
        try:
            self.ws.stop_manager_with_all_streams()
        except Exception as e:
            logger.error("stop error: %s", e)

    # helpers
    def _ws_symbol(self, symbol):
        return re.sub(r"[/_]", "", symbol)

    def _ws_symbols(self, symbols):
        if isinstance(symbols, str):
            return [symbols]
        if isinstance(symbols, list):
            return [self._ws_symbol(m) for m in symbols]
        raise Exception(f"cannot parse symbols {symbols}")

    def _stream_handle(self, raw, parser, callback, **kwargs):
        raw = json.loads(raw)
        if "result" in raw and not raw["result"]:
            return

        data = parser(raw["data"])
        if not data:
            return
        asyncio.ensure_future(callback(data=data, **kwargs))

    # API function
    # Account
    async def subscribe_account(self, callback, **kwargs):
        self.ws.create_stream(
            channels="arr",
            markets="!userData",
            process_stream_data=lambda data: self._stream_handle(
                data,
                self._parse_account,
                callback,
            ),
            api_key=self.exchange.apiKey,
            api_secret=self.exchange.secret,
        )

    async def unsubscribe_account(self, **kwargs):
        pass

    def _parse_account(self, data):
        print("_parse_account", data)

    # Bars
    async def subscribe_ohlcv(self, symbols, timeframe, callback, **kwargs):
        symbols = self._ws_symbols(symbols)
        self.ws.create_stream(
            channels=[f"kline_{timeframe}"],
            markets=symbols,
            process_stream_data=lambda data: self._stream_handle(
                data,
                self._parse_ohlcv,
                callback,
                symbols=symbols,
                timeframe=timeframe,
            ),
            api_key=self.exchange.apiKey,
            api_secret=self.exchange.secret,
        )

    async def unsubscribe_ohlcv(self, symbols, timeframe, **kwargs):
        pass

    def _parse_ohlcv(self, data):
        print("---> _parse_ohlcv:", data)
        kline = self.exchange.safe_value(data, "k")
        symbol = self.exchange.safe_string_2(kline, "s", "ps")
        timeframe = self.exchange.safe_string(kline, "i")
        bar = [
            self.exchange.safe_integer(kline, "t"),
            self.exchange.safe_float(kline, "o"),
            self.exchange.safe_float(kline, "h"),
            self.exchange.safe_float(kline, "l"),
            self.exchange.safe_float(kline, "c"),
            self.exchange.safe_float(kline, "v"),
        ]
        return dict(
            symbol=symbol,
            timeframe=timeframe,
            bar=bar,
        )

    # Tickers
    async def subscribe_tickers(self, symbols, callback, **kwargs):
        symbols = self._ws_symbols(symbols)
        self.ws.create_stream(
            channels=["ticker"],
            markets=symbols,
            process_stream_data=lambda data: self._stream_handle(
                data,
                self._parse_ticker,
                callback,
            ),
            api_key=self.exchange.apiKey,
            api_secret=self.exchange.secret,
        )

    async def unsubscribe_tickers(self, symbols, **kwargs):
        pass

    def _parse_ticker(self, data):
        return self.exchange.parse_ws_ticker(data, self.market_type)


class PyBinance(PyBinanceWS, metaclass=Singleton):
    pass
