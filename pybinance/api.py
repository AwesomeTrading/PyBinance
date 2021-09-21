from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import time
import logging
import threading
import queue
import re
import json
import math
import traceback
from functools import wraps

import ccxt
from ccxt.base.errors import NetworkError, ExchangeError
from unicorn_binance_websocket_api import BinanceWebSocketApiManager

logger = logging.getLogger('PyBinance')


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton,
                                        cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class PyBinanceAPI:
    def __init__(
        self,
        exchange,
        currency,
        config,
        retries=5,
        sandbox=False,
        **kwargs,
    ):
        self.exchange = getattr(ccxt, exchange)(config)
        self.exchange_type = self.exchange.options['defaultType']
        self.currency = currency
        self.retries = retries
        if sandbox:
            self.exchange.set_sandbox_mode(True)

    def retry(method):
        @wraps(method)
        def retry_method(self, *args, **kwargs):
            for i in range(self.retries):
                # if self.debug:
                #     print('{} - {} - Attempt {}'.format(
                #         datetime.now(), method.__name__, i))
                time.sleep(self.exchange.rateLimit / 1000)
                try:
                    return method(self, *args, **kwargs)
                except (NetworkError, ExchangeError):
                    if i == self.retries - 1:
                        raise

        return retry_method

    @retry
    def fetch_ohlcv(
        self,
        symbol,
        timeframe,
        since=None,
        limit=None,
        params={},
    ):
        # spot api using milliseconds
        # future api using second timestamp
        if since is not None:
            if self.exchange_type == 'spot':
                since = math.floor(since * 1000)

        return self.exchange.fetch_ohlcv(symbol,
                                         timeframe=timeframe,
                                         since=since,
                                         limit=limit,
                                         params=params)

    @retry
    def fetch_trades(self, symbol, since=None, limit=None, params={}):
        return self.exchange.fetch_trades(symbol, since, limit, params)

    @retry
    def get_time(self):
        return self.exchange.fetch_time()

    @retry
    def get_tickers(self, symbols):
        return self.exchange.fetch_tickers(symbols)

    @retry
    def get_markets(self):
        self.exchange.load_markets()
        return self.exchange.markets_by_id

    # account api
    @retry
    def get_my_wallet_balance(self, params=None):
        balance = self.exchange.fetch_balance(params)
        return balance

    @retry
    def get_my_balance(self):
        balance = self.exchange.fetch_balance()

        cash = balance['free'][self.currency]
        value = balance['total'][self.currency]
        return cash, value

    @retry
    def create_my_order(self, symbol, type, side, amount, price, params):
        return self.exchange.create_order(symbol=symbol,
                                          type=type,
                                          side=side,
                                          amount=amount,
                                          price=price,
                                          params=params)

    @retry
    def fetch_my_order(self, oid, symbol):
        return self.exchange.fetch_order(oid, symbol)

    @retry
    def fetch_my_orders(self, symbol=None, since=None, limit=None, params={}):
        return self.exchange.fetch_orders(symbol, since, limit, params)

    @retry
    def fetch_my_open_orders(self,
                             symbol=None,
                             since=None,
                             limit=None,
                             params={}):
        return self.exchange.fetch_open_orders(symbol, since, limit, params)

    @retry
    def cancel_my_order(self, id, symbol):
        return self.exchange.cancel_order(id, symbol)

    @retry
    def cancel_my_orders(self, symbol=None, params={}):
        if 'origClientOrderIdList' in params:
            params['origClientOrderIdList'] = json.dumps(
                params['origClientOrderIdList'], separators=(",", ":"))
        return self.exchange.cancel_all_orders(symbol, params)

    @retry
    def fetch_my_trades(self, symbol=None, since=None, limit=None, params={}):
        return self.exchange.fetch_my_trades(symbol, since, limit, params)

    @retry
    def fetch_my_positions(self, symbols=None, params={}):
        positions = self.exchange.fetch_positions(symbols, params)

        response = []
        for position in positions:
            psymbol = self.exchange.safe_symbol(position['symbol'])
            if symbols is None or psymbol in symbols:
                response.append(position)
        return response


class PyBinanceWS(PyBinanceAPI):
    '''API provider for Binance feed and broker classes.'''
    def __init__(self, sandbox=False, **kwargs):
        super().__init__(exchange='binance', sandbox=sandbox, **kwargs)

        self.subscribers = {}

        # parsers
        self.parsers = {
            'kline': self._parse_bar,
            '24hrTicker': self._parse_ticker,
            '24hrMiniTicker': self._parse_miniticker,
        }
        if self.exchange_type == 'spot':
            self.parsers.update({
                'outboundAccountPosition': self._parse_spot_account,
                'executionReport': self._parse_spot_order,
            })
        elif self.exchange_type == 'future':
            self.parsers.update({
                'ACCOUNT_UPDATE': self._parse_future_account,
                'ORDER_TRADE_UPDATE': self._parse_future_order,
            })
        else:
            raise RuntimeError(
                f"Binance parser for exchange type {self.exchange_type} wasn't support"
            )

        # binance websocket init
        exchange = 'binance.com'
        if self.exchange_type == 'margin':
            exchange = f"{exchange}-margin"
        elif self.exchange_type == 'future':
            exchange = f"{exchange}-futures"

        if sandbox:
            exchange = f"{exchange}-testnet"

        print(f"Binance exchange: {exchange}")
        self.ws = BinanceWebSocketApiManager(exchange=exchange)

        self._loop_stream()

    ### low level functions
    def _parse_ws_symbol(self, symbol):
        return re.sub(r"[/_]", '', symbol)

    def _parse_ws_symbols(self, symbols):
        if isinstance(symbols, str):
            return [symbols]

        if isinstance(symbols, list):
            return [self._parse_ws_symbol(m) for m in symbols]
        raise Exception(f'cannot parse symbols {symbols}')

    # subscribe
    # account
    def subscribe_my_account(self, **kwargs):
        if self.exchange_type == 'spot':
            events = ['executionReport', 'outboundAccountPosition']
        elif self.exchange_type == 'future':
            events = ['ACCOUNT_UPDATE', 'ORDER_TRADE_UPDATE']
        else:
            raise RuntimeError(
                f"Event subscribers for exchange type {self.exchange_type} wasn't support"
            )

        return self.subscribe(['arr'], ['!userData'], events, **kwargs)

    # https://binance-docs.github.io/apidocs/futures/en/#event-order-update
    def _parse_future_account(self, e):
        if e['e'] != 'ACCOUNT_UPDATE':
            raise RuntimeError(f"event {e} is not ACCOUNT_UPDATE")
        a = e['a']

        balances = []
        for b in a['B']:
            if b['a'] == self.currency:
                balance = dict(
                    asset=b['a'],
                    wallet=float(b['wb']),  # Wallet Balance
                    cross=float(b['cw']),  # Cross Wallet Balance
                )
                balances.append(balance)

        positions = []
        for p in a['P']:
            positions.append(
                dict(
                    id=p["s"],
                    symbol=self.exchange.safe_symbol(p["s"]),  # Symbol
                    amount=float(p["pa"]),  # Position Amount
                    price=float(p["ep"]),  # Entry Price
                    accum=float(p["cr"]),  # (Pre-fee) Accumulated Realized
                    pnl=float(p["up"]),  # Unrealized PnL
                    margin_type=p["mt"],  # Margin Type
                    isolated=float(
                        p["iw"]),  # Isolated Wallet (if isolated position)
                    side=p["ps"],  # Position Side
                ))

        return dict(
            event=e['e'],
            event_time=e['E'],
            transaction_time=e['T'],
            account=dict(
                reason=a['m'],  # Event reason type
                balances=balances,
                positions=positions,
            ))

    def _parse_spot_account(self, e):
        if e['e'] != 'outboundAccountPosition':
            raise RuntimeError(f"Event {e} is not outboundAccountPosition")

        balances = []
        positions = []
        for b in e['B']:
            if b['a'] == self.currency:
                balance = dict(
                    asset=b['a'],
                    # Free Balance
                    wallet=float(b['f']),
                    # Locked Balance
                    locked=float(b['l']),
                    # Cross Wallet Balance
                    cross=float(b['f']) + float(b['l']),
                )

                balances.append(balance)
            else:
                symbol = f"{b['a']}{self.currency}"
                positions.append(
                    dict(
                        id=symbol,
                        symbol=self.exchange.safe_symbol(symbol),  # Symbol
                        amount=float(b['f']),  # Position Amount
                        price=0,  # Entry Price
                        accum=0,  # (Pre-fee) Accumulated Realized
                        pnl=0,  # Unrealized PnL
                        # margin_type=',  # Margin Type
                        isolated=float(b['l']),  # Isolated/Locked Wallet
                        side='BUY',  # Position Side
                    ))

        return dict(
            event=e['e'],
            event_time=e['E'],
            transaction_time=e['u'],
            account=dict(
                # reason=a['m'],  # Event reason type
                balances=balances,
                positions=positions,
            ))

    # order
    def _parse_future_order(self, e):
        if e['e'] != 'ORDER_TRADE_UPDATE':
            raise RuntimeError(f"event {e} is not ORDER_TRADE_UPDATE")
        o = e['o']
        return dict(event=e['e'],
                    event_time=e['E'],
                    timestamp=e['T'],
                    order=dict(
                        id=o["i"],
                        clientOrderId=o["c"],
                        symbol=self.exchange.safe_symbol(o['s']),
                        time=o["T"],
                        side=o["S"],
                        type=o["o"],
                        status=o["X"],
                        price=float(o["p"]),
                        stopPrice=float(o["sp"]),
                        amount=float(o["q"]),
                        filled=float(o["l"]),
                        profit=float(o["rp"]),
                        timeInForce=o["f"],
                        average=float(o["ap"]),
                        execType=o["x"],
                        cost=float(o["z"]),
                        lastPrice=float(o["L"]),
                        tradeid=o["t"],
                        bid=float(o["b"]),
                        ask=float(o["a"]),
                        maker=o["m"],
                        reduceOnly=o["R"],
                        workType=o["wt"],
                        originType=o["ot"],
                        positionSide=o["ps"],
                        closePosition=o["cp"],
                        commAsset=o.get("N", None),
                        comm=o.get("n", None),
                        activePrice=float(o.get("AP", 0)),
                        callRate=o.get("cr", None),
                    ))

    def _parse_spot_order(self, e):
        if e['e'] != 'executionReport':
            raise RuntimeError(f"event {e} is not executionReport")

        return dict(
            event=e['e'],
            event_time=e['E'],
            timestamp=e['T'],
            order=dict(
                id=e["i"],
                clientOrderId=e["c"],
                symbol=self.exchange.safe_symbol(e['s']),
                time=e["T"],
                side=e["S"],
                type=e["o"],
                status=e["X"],
                price=float(e["p"]),
                stopPrice=float(e["P"]),
                amount=float(e["q"]),
                filled=float(e["l"]),
                profit=0,
                timeInForce=e["f"],
                average=float(e["L"]),  # last price
                execType=e["x"],
                cost=float(e["z"]),
                lastPrice=float(e["L"]),
                tradeid=e["t"],
                # bid=float(o["b"]),
                # ask=float(o["a"]),
                maker=e["m"],
                # reduceOnly=o["R"],
                # workType=o["wt"],
                # originType=o["ot"],
                # positionSide=o["ps"],
                # closePosition=o["cp"],
                comm=e.get("n", None),
                commAsset=e.get("N", None),
                # activePrice=float(o.get("AP", 0)),
                # callRate=o.get("cr", None),
            ))

    # bar
    def subscribe_bars(self, markets, timeframe, q=None, **kwargs):
        markets = self._parse_ws_symbols(markets)
        channel = f"kline_{timeframe}"
        listeners = self._bar_listeners(markets, timeframe)
        return self.subscribe(channel, markets, listeners, q=q, **kwargs)

    def _bar_listeners(self, markets, timeframe):
        channels = []
        for m in markets:
            channels.append(self._bar_listener(m, timeframe))
        return channels

    def _bar_listener(self, market, timeframe):
        return f"kline_{market}_{timeframe}"

    def _parse_bar(self, e):
        if e['e'] != 'kline':
            raise RuntimeError(f"event {e} is not kline")
        b = e['k']

        event = dict(
            event=e['e'],
            event_time=e['E'],
            symbol=e['s'],
            bar=dict(
                start=b["t"],  # Kline start time
                end=b["T"],  # Kline close time
                symbol=self.exchange.safe_symbol(b["s"]),  # Symbol
                timeframe=b["i"],  # timeframe
                first_tradeid=b["f"],  # First trade ID
                last_tradeid=b["L"],  # Last trade ID
                open=float(b["o"]),  # Open price
                close=float(b["c"]),  # Close price
                high=float(b["h"]),  # High price
                low=float(b["l"]),  # Low price
                volume=float(b["v"]),  # Base asset volume
                trades=b["n"],  # Number of trades
                closed=b["x"],  # Is this kline closed?
                quote_volume=float(b["q"]),  # Quote asset volume
                taker_base_volume=b["V"],  # Taker buy base asset volume
                taker_quote_volume=b["Q"],  # Taker buy quote asset volume
                ignore=b["B"],  # Ignore
            ))
        event['listeners'] = [
            "kline",
            self._bar_listener(event['symbol'], event['bar']['timeframe'])
        ]
        return event

    # ticker
    def subscribe_tickers(self, markets, **kwargs):
        markets = self._parse_ws_symbols(markets)
        return self.subscribe('ticker', markets, ['24hrTicker'], **kwargs)

    def _parse_ticker(self, e):
        if e['e'] != '24hrTicker':
            raise RuntimeError(f"event {e} is not 24hrTicker")
        return dict(
            event=e['e'],
            event_time=e['E'],
            symbol=self.exchange.safe_symbol(e["s"]),  # Symbol
            change=float(e["p"]),  # Price change
            change_percent=float(e["P"]),  # Price change percent
            avg_price=float(e["w"]),  # Weighted average price
            last=float(e["c"]),  # Last price
            quantity=float(e["Q"]),  # Last quantity
            open=float(e["o"]),  # Open price
            high=float(e["h"]),  # High price
            low=float(e["l"]),  # Low price
            volume=float(e["v"]),  # Total traded base asset volume
            quote_volume=float(e["q"]),  # Total traded quote asset volume
            open_time=e["O"],  # Statistics open time
            close_time=e["C"],  # Statistics close time
            first_tradeid=e["F"],  # First trade ID
            last_tradeid=e["L"],  # Last trade Id
            trades=e["n"],  # Total number of trades
        )

    # mini ticker
    def subscribe_minitickers(self, markets, **kwargs):
        markets = self._parse_ws_symbols(markets)
        return self.subscribe('miniTicker', markets, ['24hrMiniTicker'],
                              **kwargs)

    def _parse_miniticker(self, e):
        if e['e'] != '24hrMiniTicker':
            raise RuntimeError(f"event {e} is not 24hrMiniTicker")
        return dict(
            event=e['e'],
            event_time=e['E'],
            symbol=self.exchange.safe_symbol(e["s"]),  # Symbol
            close=float(e["c"]),  # Close price
            open=float(e["o"]),  # Open price
            high=float(e["h"]),  # High price
            low=float(e["l"]),  # Low price
            volume=float(e["v"]),  # Total traded base asset volume
            quote_volume=float(e["q"]),  # Total traded quote asset volume
        )

    # book ticker
    def subscribe_bookticker(self, markets, **kwargs):
        markets = self._parse_ws_symbols(markets)
        return self.subscribe('bookTicker', markets, ['bookTicker'], **kwargs)

    def _parse_bookticker(self, e):
        if e['e'] != 'bookTicker':
            raise RuntimeError(f"event {e} is not bookTicker")
        return dict(
            event=e['e'],
            event_time=e['E'],
            transaction_time=e['T'],
            update_id=e["u"],
            symbol=self.exchange.safe_symbol(e["s"]),
            bid=float(e["b"]),
            bid_qty=float(e["B"]),
            ask=float(e["a"]),
            ask_qty=float(e["A"]),
        )

    # subscribe
    def _rate_limit(self):
        if self.exchange.enableRateLimit:
            self.exchange.throttle()
            self.exchange.lastRestRequestTimestamp = self.exchange.milliseconds(
            )

    def subscribe(self,
                  channels,
                  markets,
                  events,
                  q=None,
                  label=None,
                  **kwargs):
        self._rate_limit()

        reused = False
        if q is not None:
            reused = True
        elif q is None:
            q = queue.Queue()

        # label of stream
        if not label:
            label = ""
            if len(channels) > 0:
                label += ",".join(markets) if type(
                    channels) == list else channels
            if len(markets) > 0:
                label += "->" + ",".join(markets)
            else:
                logger.warn(
                    "Subscribe request label is common "
                    "and could be unsubscribe by another process "
                    f"channel={channels}, events={events}, label={label}")

        # subscribe
        sid = self.ws.create_stream(channels,
                                    markets,
                                    stream_label=label,
                                    output="dict",
                                    api_key=self.exchange.apiKey,
                                    api_secret=self.exchange.secret,
                                    **kwargs)
        # set event listener
        for e in events:
            if e not in self.subscribers:
                self.subscribers[e] = []

            existed = False
            if reused:
                for sq in self.subscribers[e]:
                    if sq == q:
                        existed = True
                        break

            if not existed:
                self.subscribers[e].append(q)
        return q, sid

    def unsubscribe(self, stream_id, channels=[], markets=[], **kwargs):
        if not channels and not markets:
            ok = self.ws.stop_stream(stream_id)
        else:
            if type(markets) == str:
                markets = [markets]
            markets = self._parse_ws_symbols(markets)

            self._rate_limit()
            ok = self.ws.unsubscribe_from_stream(stream_id, channels, markets,
                                                 **kwargs)

        self.ws.wait_till_stream_has_stopped(stream_id)

        # logger.info(self.ws.print_summary(disable_print=True))
        return ok

    # stream data loop
    def _loop_stream(self):
        t = threading.Thread(target=self._t_loop_stream)
        t.daemon = True
        t.start()

    def _t_loop_stream(self):
        while True:
            if self.ws.is_manager_stopping():
                self.stop()
                return

            buffer = self.ws.pop_stream_data_from_stream_buffer()
            if buffer == False:
                time.sleep(0.01)
                continue
            if buffer is not None:
                try:
                    # skip unwanted data
                    if 'result' in buffer and buffer['result'] is None:
                        continue

                    # handle msg
                    if 'data' in buffer:
                        buffer = buffer['data']

                    if 'e' in buffer:
                        name = buffer['e']
                    else:
                        raise RuntimeError(
                            f"buffer format is invalid: {buffer}")

                    if name not in self.parsers:
                        logger.warn("event parser not found: %s", buffer)
                        continue

                    event = self.parsers[name](buffer)

                    if 'listeners' in event:
                        listeners = event['listeners']
                    else:
                        listeners = [name]

                    for listener in listeners:
                        if listener not in self.subscribers:
                            continue

                        for q in self.subscribers[listener]:
                            q.put(event)
                except Exception as e:
                    logger.error("raw data: %s", buffer)
                    logger.error("exception: %s", e)
                    traceback.print_exc()

    def stop(self):
        self.ws.stop_manager_with_all_streams()


class PyBinance(PyBinanceWS, metaclass=Singleton):
    pass
