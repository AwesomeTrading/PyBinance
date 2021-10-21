from setuptools import setup
from pip._internal.req import parse_requirements

install_requires = parse_requirements('requirements.txt', session='hack')
install_requires = [str(ir.requirement) for ir in install_requires]

setup(
    name='pybinance',
    version='1.0',
    description='Python Binance Websocket broker',
    url='https://github.com/AwesomeTrading/PyBinance.git',
    author='Santatic',
    license='Private',
    packages=['pybinance'],
    install_requires=install_requires,
)