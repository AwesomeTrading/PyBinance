from setuptools import setup

setup(
    name='pybinance',
    version='1.0',
    description='Python Binance Websocket broker',
    url='https://github.com/AwesomeTrading/PyBinance.git',
    author='Santatic',
    license='Private',
    packages=['pybinance'],
    install_requires=['ccxt', 'unicorn-binance-websocket-api==1.33.1'],
)