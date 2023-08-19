env:
	conda create -n PyBinance python=3.11
	conda activate PyBinance
	pip install -r requirements.txt

test:
	python test.py
