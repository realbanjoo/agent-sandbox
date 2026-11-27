# Tests run from the source tree (no install). `make wheel` builds the package as an installer would.
.PHONY: check wheel
check:
	PYTHONPATH=src python3 -B -m unittest discover -s tests -v

wheel:
	python3 -m pip wheel --no-deps -q -w dist .
